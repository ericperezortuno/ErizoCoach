"""Estadísticas compartidas: el paquete, su validación, la descarga y la publicación."""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from erizocoach.builds.models import ItemCount
from erizocoach.config import load_settings
from erizocoach.stats import pack
from erizocoach.stats.aggregate import ChampionStats, Stats, stats_payload
from erizocoach.stats.pack import PackError, SharedStats, decode_pack, encode_pack
from erizocoach.stats.publish import MIN_MATCHES, PackPublisher, PublishTarget
from erizocoach.stats.service import StatsService, choose
from erizocoach.stats.source import StatsBuildSource

from .stats_helpers import fill_store

AHRI, ZED = 103, 238
RUNES = (8100, 8300, 8112, 8143, 8138, 8135, 8313, 8347, 5008, 5008, 5001)


def entry(champion: int = AHRI, position: str = "MIDDLE", games: int = 40) -> ChampionStats:
    return ChampionStats(
        champion, position, games, games // 2, (ItemCount(1056), ItemCount(2003, 2)), 3020, 0.9,
        (6655, 4645, 3089), (3135, 3157), games, RUNES, (4, 14),
    )  # fmt: skip


def stats(patch: str = "16.22", matches: int = 500, *entries: ChampionStats) -> Stats:
    chosen = entries or (entry(),)
    return patch, matches, {(e.champion_id, e.position): e for e in chosen}


def gz(payload: Any) -> bytes:
    return gzip.compress(json.dumps(payload).encode("utf-8"))


# --- Paquete ------------------------------------------------------------------------------


def test_pack_roundtrip_is_small_and_reproducible() -> None:
    original = stats("16.22", 500, entry(), entry(ZED, "MIDDLE", 25))
    data = encode_pack(original)
    assert decode_pack(data) == original
    assert len(data) < 2_000


def test_pack_carries_no_player_data() -> None:
    text = gzip.decompress(encode_pack(stats())).decode("utf-8")
    assert "puuid" not in text.lower() and "EUW1_" not in text and "gameName" not in text


def test_invalid_entries_are_dropped_and_valid_ones_kept() -> None:
    payload = stats_payload(*stats("16.22", 500, entry(), entry(ZED, "MIDDLE")))
    builds = payload["builds"]
    builds["238:MIDDLE"]["core"] = [1, 2, 3, 4, 5]  # demasiados
    builds["1:NOWHERE"] = {**builds["103:MIDDLE"], "champion_id": 1, "position": "NOWHERE"}
    builds["2:TOP"] = {**builds["103:MIDDLE"], "champion_id": 2, "position": "TOP", "wins": 99}
    builds["3:TOP"] = {**builds["103:MIDDLE"], "champion_id": 3, "position": "TOP",
                       "runes": [-1] * 11}  # fmt: skip
    patch, matches, kept = decode_pack(gz(payload))
    assert (patch, matches) == ("16.22", 500) and list(kept) == [(AHRI, "MIDDLE")]


@pytest.mark.parametrize(
    "data",
    [
        b"no es gzip",
        gz({"format": 999, "patch": "16.22", "matches": 5, "builds": {}}),
        gz(stats_payload("<script>", 500, stats()[2])),
        gz(stats_payload("16.22", 0, stats()[2])),
        gz(stats_payload("16.22", 500, {})),
        gz([1, 2, 3]),
        gzip.compress(b"{" * 10),
    ],
)
def test_broken_or_foreign_packs_are_rejected(data: bytes) -> None:
    with pytest.raises(PackError):
        decode_pack(data)


def test_compression_bombs_are_rejected() -> None:
    bomb = gzip.compress(b" " * (pack.MAX_UNPACKED + 1))
    assert len(bomb) < pack.MAX_DOWNLOAD
    with pytest.raises(PackError, match="descomprimido"):
        decode_pack(bomb)
    with pytest.raises(PackError, match="grande"):
        decode_pack(b"\0" * (pack.MAX_DOWNLOAD + 1))


# --- Descarga y copia local ---------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch) -> list[bytes | Exception]:
    """Respuestas que dará la «descarga», en orden; registra cuántas se piden."""
    answers: list[bytes | Exception] = []

    async def fake(_url: str) -> bytes:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(pack, "download_pack", fake)
    return answers


async def test_downloads_validates_and_keeps_a_local_copy(
    tmp_path: Path, downloads: list[bytes | Exception]
) -> None:
    clock = Clock()
    downloads.append(encode_pack(stats("16.22", 500)))
    shared = SharedStats("https://x", tmp_path, refresh_seconds=3600, clock=clock)
    first = await shared.get()
    assert first is not None and first[:2] == ("16.22", 500)
    assert (tmp_path / pack.CACHE_FILE).exists()
    assert await shared.get() == first and not downloads  # dentro de la hora: sin descargar

    clock.now += 3601
    downloads.append(OSError("sin red"))
    assert await shared.get() == first  # sin red: sigue con lo último bueno
    clock.now += 3601
    downloads.append(b"basura")
    assert await shared.get() == first  # paquete dañado: no se usa ni se guarda
    assert decode_pack((tmp_path / pack.CACHE_FILE).read_bytes()) == first


async def test_starts_from_the_local_copy_without_internet(
    tmp_path: Path, downloads: list[bytes | Exception]
) -> None:
    (tmp_path / pack.CACHE_FILE).write_bytes(encode_pack(stats("16.21", 800)))
    os.utime(tmp_path / pack.CACHE_FILE, (999_000.0, 999_000.0))
    clock = Clock()
    shared = SharedStats("https://x", tmp_path, refresh_seconds=3600, clock=clock)
    downloads.append(OSError("sin red"))
    result = await shared.get()
    assert result is not None and result[:2] == ("16.21", 800)
    assert len(downloads) == 1  # copia reciente: ni lo intenta


# --- Qué estadísticas se usan --------------------------------------------------------------


def test_choose_prefers_enough_data_then_newest_patch() -> None:
    own_few, shared = stats("16.22", 30), stats("16.22", 900)
    assert choose("16.22", own_few, shared) is shared  # las propias, con pocas partidas
    own = stats("16.22", 400)
    assert choose("16.22", own, stats("16.21", 3000)) is own  # parche actual primero
    assert choose("16.22", own, stats("16.22", 400)) is own  # empate: las propias
    assert choose("16.22", stats("16.23", 3000), own) is own  # más nuevo que tu cliente
    assert choose("16.22", None, shared) is shared and choose("16.22", None, None) is None


class Connector:
    def on_connect(self, _callback: object) -> None:
        return None


async def test_service_uses_shared_stats_without_a_key(
    tmp_path: Path, downloads: list[bytes | Exception]
) -> None:
    downloads.append(encode_pack(stats("16.22", 700)))
    source = StatsBuildSource()
    shared = SharedStats("https://x", tmp_path / "s")
    service = StatsService(Connector(), None, source, tmp_path, shared=shared)  # type: ignore[arg-type]
    await service.refresh("16.22")
    assert source.patch == "16.22" and source.matches == 700 and source.revision == 1
    await service.refresh("16.22")
    assert source.revision == 1  # nada nuevo: las builds no se recalculan


async def test_service_prefers_your_own_stats_when_they_are_enough(
    tmp_path: Path, downloads: list[bytes | Exception]
) -> None:
    from .gamedata_fixture import loaded_gamedata

    fill_store(tmp_path / "matches.sqlite3", matches=120)
    downloads.append(encode_pack(stats("16.22", 60)))
    source = StatsBuildSource()
    service = StatsService(
        Connector(), loaded_gamedata(), source, tmp_path,  # type: ignore[arg-type]
        shared=SharedStats("https://x", tmp_path / "s"),
    )  # fmt: skip
    await service.refresh("16.22")
    assert source.matches == 120


# --- Publicación ----------------------------------------------------------------------------


class FakeGit:
    def __init__(self, fail_on: str = "") -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.pushed: bytes = b""
        self._fail_on = fail_on

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        self.envs.append(kwargs["env"])
        verb = command[3]
        if verb == "push":
            self.pushed = (Path(kwargs["cwd"]) / pack.PACK_FILE).read_bytes()
        code = 1 if verb == self._fail_on else 0
        return subprocess.CompletedProcess(command, code, "", "Permission denied (publickey)")


def publisher(tmp_path: Path, git: FakeGit, clock: Clock) -> PackPublisher:
    key = tmp_path / "clave"
    key.write_text("x", encoding="utf-8")
    target = PublishTarget("git@github.com:alguien/ErizoCoach-datos.git", key)
    return PackPublisher(target, tmp_path, runner=git, clock=clock, every_seconds=3600)


@pytest.mark.skipif(shutil.which("git") is None, reason="hace falta git")
def test_publishes_when_it_makes_sense(tmp_path: Path) -> None:
    git, clock = FakeGit(), Clock()
    publisher_ = publisher(tmp_path, git, clock)
    assert not publisher_.maybe_publish(stats("16.22", MIN_MATCHES - 1))  # pocas partidas
    assert publisher_.maybe_publish(stats("16.22", 500))
    assert decode_pack(git.pushed)[:2] == ("16.22", 500)
    push = git.calls[-1]
    assert push[3:] == ["push", "-q", "--force", "git@github.com:alguien/ErizoCoach-datos.git",
                        "HEAD:main"]  # fmt: skip
    assert "IdentitiesOnly=yes" in git.envs[-1]["GIT_SSH_COMMAND"]
    assert "BatchMode=yes" in git.envs[-1]["GIT_SSH_COMMAND"]

    assert not publisher_.maybe_publish(stats("16.22", 500))  # lo mismo: nada
    assert not publisher_.maybe_publish(stats("16.22", 600))  # más datos, pero muy pronto
    assert publisher_.maybe_publish(stats("16.23", 150))  # parche nuevo: enseguida
    clock.now += 3601
    assert publisher_.maybe_publish(stats("16.23", 900))


@pytest.mark.skipif(shutil.which("git") is None, reason="hace falta git")
def test_failed_push_is_retried_later(tmp_path: Path) -> None:
    git = FakeGit(fail_on="push")
    publisher_ = publisher(tmp_path, git, Clock())
    assert not publisher_.maybe_publish(stats("16.22", 500))
    git._fail_on = ""
    assert publisher_.maybe_publish(stats("16.22", 500))


@pytest.mark.skipif(shutil.which("git") is None, reason="hace falta git")
def test_real_git_push_keeps_a_single_commit(tmp_path: Path) -> None:
    remote = tmp_path / "datos.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    key = tmp_path / "clave"
    key.write_text("x", encoding="utf-8")
    clock = Clock()
    publisher_ = PackPublisher(PublishTarget(str(remote), key), tmp_path, clock=clock)
    assert publisher_.maybe_publish(stats("16.22", 500))
    assert publisher_.maybe_publish(stats("16.23", 300))

    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", "--git-dir", str(remote), *args], check=True, capture_output=True
        ).stdout

    assert git("rev-list", "--count", "main").strip() == b"1"
    assert decode_pack(git("show", f"main:{pack.PACK_FILE}"))[:2] == ("16.23", 300)
    assert b"16.23" in git("show", "main:README.md")


def test_publish_settings_need_both_values(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('stats_publish_repo = "git@x:y/z.git"\n', "utf-8")
    settings, warnings = load_settings(data_dir=tmp_path)
    assert settings.stats_publish_repo == "" and warnings
    (tmp_path / "config.toml").write_text(
        'stats_publish_repo = "git@x:y/z.git"\nstats_publish_key = "C:/k"\nstats_shared = false\n',
        "utf-8",
    )
    settings, warnings = load_settings(data_dir=tmp_path)
    assert settings.stats_publish_repo == "git@x:y/z.git" and settings.stats_publish_key == Path(
        "C:/k"
    )
    assert not settings.stats_shared and not warnings
