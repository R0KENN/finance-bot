"""Копия базы админу: уходит раз в сутки, не оставляет файлов и не шлётся чужим."""
import gzip
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram.methods import SendDocument

from finbot import config
from finbot.services import backup
from tests.harness import Harness

TZ = ZoneInfo("Europe/Moscow")


def make_db(folder: str) -> Path:
    path = Path(folder) / "finbot.db"
    conn = sqlite3.connect(path)
    conn.execute("create table t(a)")
    conn.execute("insert into t values (42)")
    conn.commit()
    conn.close()
    return path


class BackupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness.create()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = make_db(self.tmp.name)
        backup._retry_at = 0.0

    async def asyncTearDown(self):
        await self.h.close()
        self.tmp.cleanup()

    def sent(self):
        return [c for c in self.h.session.calls if isinstance(c, SendDocument)]

    async def test_snapshot_is_valid_gzip_of_database(self):
        data = backup._snapshot(str(self.db))
        restored = Path(self.tmp.name) / "restored.db"
        restored.write_bytes(gzip.decompress(data))
        conn = sqlite3.connect(restored)
        try:
            self.assertEqual(conn.execute("select a from t").fetchall(), [(42,)])
        finally:
            conn.close()

    async def test_sent_once_a_day_after_the_hour(self):
        now = datetime(2026, 10, 1, 4, 5, tzinfo=TZ)
        kw = dict(admin_id=777, db_path=self.db, hour=4)
        self.assertTrue(await backup.tick(self.h.bot, now=now, **kw))
        self.assertFalse(await backup.tick(self.h.bot, now=now.replace(minute=30), **kw))   # тот же день
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(self.sent()[0].chat_id, 777)
        self.assertTrue(await backup.tick(self.h.bot, now=datetime(2026, 10, 2, 4, 1, tzinfo=TZ), **kw))
        self.assertEqual(len(self.sent()), 2)

    async def test_not_before_the_hour(self):
        now = datetime(2026, 10, 1, 3, 59, tzinfo=TZ)
        self.assertFalse(await backup.tick(self.h.bot, admin_id=777, db_path=self.db, hour=4, now=now))
        self.assertEqual(self.sent(), [])

    async def test_disabled_without_admin(self):
        now = datetime(2026, 10, 1, 12, 0, tzinfo=TZ)
        self.assertFalse(await backup.tick(self.h.bot, admin_id=0, db_path=self.db, hour=4, now=now))
        self.assertEqual(self.sent(), [])

    async def test_leaves_no_copies_on_disk(self):
        await backup.tick(self.h.bot, admin_id=777, db_path=self.db, hour=0,
                          now=datetime(2026, 10, 1, 5, 0, tzinfo=TZ))
        left = sorted(p.name for p in Path(self.tmp.name).iterdir())
        self.assertEqual(left, ["finbot.db", "finbot.db.backup-day"])

    async def test_failure_is_retried_later_not_marked_done(self):
        now = datetime(2026, 10, 1, 5, 0, tzinfo=TZ)
        broken = Path(self.tmp.name) / "missing" / "x.db"
        self.assertFalse(await backup.tick(self.h.bot, admin_id=777, db_path=broken, hour=4, now=now))
        self.assertFalse(backup._marker(broken).exists())
        self.assertGreater(backup._retry_at, 0)


class BackupCommandTests(BackupTests):
    """Команда /backup: админу копия сразу, остальным — тишина."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._saved = (config.ADMIN_ID, config.DB_PATH)
        config.ADMIN_ID, config.DB_PATH = 1, self.db

    async def asyncTearDown(self):
        config.ADMIN_ID, config.DB_PATH = self._saved
        await super().asyncTearDown()

    async def test_admin_gets_file(self):
        await self.h.client(1, "Админ").say("/backup")
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(self.sent()[0].chat_id, 1)

    async def test_stranger_gets_nothing(self):
        await self.h.client(2, "Чужой").say("/backup")
        self.assertEqual(self.sent(), [])


if __name__ == "__main__":
    unittest.main()
