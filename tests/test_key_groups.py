import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard_key_groups import KeyGroupError, KeyGroupStore, build_key_usage_workspace


class KeyGroupStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = KeyGroupStore(str(Path(self.temp_dir.name) / "monitor.db"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_groups_and_assignments_are_scoped_to_the_newapi_user(self):
        customer = self.store.create_group(9, "客户项目", "blue")
        internal = self.store.create_group(9, "内部工具", "violet")

        self.store.assign_tokens(9, [7, 8], [customer["id"]])
        self.store.assign_tokens(9, [8], [customer["id"], internal["id"]])

        self.assertEqual(
            {7: [customer["id"]], 8: [customer["id"], internal["id"]]},
            self.store.membership_map(9),
        )
        self.assertEqual([], self.store.list_groups(10))
        counts = {group["name"]: group["key_count"] for group in self.store.list_groups(9)}
        self.assertEqual({"客户项目": 2, "内部工具": 1}, counts)
        with self.assertRaisesRegex(KeyGroupError, "group not found"):
            self.store.assign_tokens(10, [7], [customer["id"]])

    def test_delete_group_keeps_keys_but_removes_their_monitor_group(self):
        temporary = self.store.create_group(9, "临时", "amber")
        retained = self.store.create_group(9, "保留", "blue")
        self.store.assign_tokens(9, [7, 8], [temporary["id"], retained["id"]])

        deleted = self.store.delete_group(9, temporary["id"])

        self.assertEqual("临时", deleted["name"])
        self.assertEqual({7: [retained["id"]], 8: [retained["id"]]}, self.store.membership_map(9))
        self.assertEqual(["保留"], [item["name"] for item in self.store.list_groups(9)])

    def test_group_can_be_renamed_and_deleted_keys_can_be_unassigned(self):
        group = self.store.create_group(9, "旧名称", "slate")
        self.store.assign_tokens(9, [7, 8], [group["id"]])

        updated = self.store.update_group(9, group["id"], "新名称", "emerald")
        self.store.remove_tokens(9, [7])

        self.assertEqual("新名称", updated["name"])
        self.assertEqual("emerald", updated["color"])
        self.assertEqual({8: [group["id"]]}, self.store.membership_map(9))

    def test_replaces_members_of_one_group_without_touching_other_groups(self):
        customer = self.store.create_group(9, "客户", "blue")
        internal = self.store.create_group(9, "内部", "emerald")
        self.store.assign_tokens(9, [7], [customer["id"], internal["id"]])
        self.store.assign_tokens(9, [8], [internal["id"]])

        changed = self.store.set_group_members(9, customer["id"], [8, 9])

        self.assertEqual(3, changed)
        self.assertEqual(
            {7: [internal["id"]], 8: [customer["id"], internal["id"]], 9: [customer["id"]]},
            self.store.membership_map(9),
        )

    def test_migrates_single_group_memberships_without_losing_assignments(self):
        database = Path(self.temp_dir.name) / "legacy.db"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE console_key_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER NOT NULL,
                    name TEXT COLLATE NOCASE NOT NULL,
                    color TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(owner_user_id, name),
                    UNIQUE(owner_user_id, id)
                );
                INSERT INTO console_key_groups VALUES (3, 9, '旧分组', 'blue', 0, 1, 1);
                CREATE TABLE console_key_group_memberships (
                    owner_user_id INTEGER NOT NULL,
                    token_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    assigned_at INTEGER NOT NULL,
                    PRIMARY KEY(owner_user_id, token_id),
                    FOREIGN KEY(owner_user_id, group_id)
                        REFERENCES console_key_groups(owner_user_id, id) ON DELETE CASCADE
                );
                INSERT INTO console_key_group_memberships VALUES (9, 7, 3, 1);
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = KeyGroupStore(str(database))
        migrated.assign_tokens(9, [7], [3, migrated.create_group(9, "新分组", "rose")["id"]])

        self.assertEqual(2, len(migrated.membership_map(9)[7]))

    def test_failed_membership_migration_rolls_back_and_can_retry(self):
        database = Path(self.temp_dir.name) / "legacy-retry.db"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE console_key_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER NOT NULL,
                    name TEXT COLLATE NOCASE NOT NULL,
                    color TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(owner_user_id, name),
                    UNIQUE(owner_user_id, id)
                );
                CREATE TABLE console_key_group_memberships (
                    owner_user_id INTEGER NOT NULL,
                    token_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    assigned_at INTEGER NOT NULL,
                    PRIMARY KEY(owner_user_id, token_id),
                    FOREIGN KEY(owner_user_id, group_id)
                        REFERENCES console_key_groups(owner_user_id, id) ON DELETE CASCADE
                );
                INSERT INTO console_key_group_memberships VALUES (9, 7, 3, 1);
                """
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(sqlite3.IntegrityError):
            KeyGroupStore(str(database))

        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            primary_key = [
                row["name"]
                for row in connection.execute("PRAGMA table_info(console_key_group_memberships)")
                if row["pk"]
            ]
            legacy_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'console_key_group_memberships_legacy'"
            ).fetchone()
            self.assertEqual(["owner_user_id", "token_id"], primary_key)
            self.assertIsNone(legacy_table)
            connection.execute(
                "INSERT INTO console_key_groups VALUES (3, 9, '恢复分组', 'blue', 0, 1, 1)"
            )
            connection.commit()
        finally:
            connection.close()

        recovered = KeyGroupStore(str(database))
        self.assertEqual({7: [3]}, recovered.membership_map(9))

    def test_group_member_limit_matches_account_key_limit(self):
        group = self.store.create_group(9, "大分组", "blue")
        self.assertEqual(2000, self.store.set_group_members(9, group["id"], list(range(1, 2001))))
        with self.assertRaisesRegex(KeyGroupError, "at most 2000"):
            self.store.set_group_members(9, group["id"], list(range(1, 2002)))

    def test_group_names_are_unique_per_user_case_insensitively(self):
        self.store.create_group(9, "Customer A", "emerald")

        with self.assertRaisesRegex(KeyGroupError, "already exists"):
            self.store.create_group(9, " customer a ", "rose")

        same_name_other_user = self.store.create_group(10, "Customer A", "rose")
        self.assertEqual(10, same_name_other_user["owner_user_id"])


class KeyUsageWorkspaceTests(unittest.TestCase):
    def test_usage_is_aggregated_by_token_id_and_current_monitor_group(self):
        groups = [
            {"id": 3, "name": "客户项目", "color": "blue", "key_count": 99},
        ]
        tokens = [
            {"id": 7, "name": "personal"},
            {"id": 8, "name": "customer-a"},
            {"id": 9, "name": "customer-b"},
        ]
        flow = [
            {"token_id": 7, "token_name": "personal", "use_group": "premium", "model_name": "gpt-5.4", "count": 2, "quota": 100, "token_used": 50},
            {"token_id": 8, "token_name": "customer-a", "use_group": "default", "model_name": "gpt-5.4", "count": 3, "quota": 200, "token_used": 80},
            {"token_id": 8, "token_name": "customer-a", "use_group": "other", "model_name": "gpt-5.5", "count": 1, "quota": 50, "token_used": 20},
            {"token_id": 9, "token_name": "customer-b", "use_group": "default", "model_name": "gpt-5.4", "count": 4, "quota": 300, "token_used": 120},
            {"token_id": 99, "token_name": "deleted-key", "use_group": "default", "model_name": "gpt-5.4", "count": 100, "quota": 9999, "token_used": 9999},
        ]

        result = build_key_usage_workspace(
            tokens=tokens,
            flow_items=flow,
            groups=groups,
            memberships={8: [3], 9: [3], 99: [3]},
            start_timestamp=100,
            end_timestamp=200,
        )

        self.assertEqual(10, result["summary"]["requests"])
        self.assertEqual(650, result["summary"]["quota"])
        self.assertEqual(2, result["token_usage"]["7"]["requests"])
        self.assertEqual([], result["token_usage"]["7"]["key_group_ids"])
        self.assertEqual(8, result["groups"][0]["usage"]["requests"])
        self.assertEqual(550, result["groups"][0]["usage"]["quota"])
        self.assertEqual(2, result["groups"][0]["usage"]["models"])
        self.assertEqual(2, result["groups"][0]["key_count"])
        self.assertEqual(2, result["ungrouped"]["usage"]["requests"])
        self.assertEqual("current_multi_membership", result["usage_attribution"])
        self.assertNotIn("use_group", result["groups"][0])
        self.assertNotIn("99", result["token_usage"])

    def test_usage_is_counted_in_each_selected_group_but_summary_is_not_duplicated(self):
        result = build_key_usage_workspace(
            tokens=[{"id": 7, "name": "shared"}],
            flow_items=[{"token_id": 7, "model_name": "gpt-5.4", "count": 4, "quota": 200, "token_used": 80}],
            groups=[
                {"id": 3, "name": "客户", "color": "blue", "key_count": 0},
                {"id": 4, "name": "项目", "color": "rose", "key_count": 0},
            ],
            memberships={7: [3, 4]},
            start_timestamp=100,
            end_timestamp=200,
        )

        self.assertEqual(4, result["summary"]["requests"])
        self.assertEqual([3, 4], result["token_usage"]["7"]["key_group_ids"])
        self.assertEqual([4, 4], [group["usage"]["requests"] for group in result["groups"]])
        self.assertEqual(0, result["ungrouped"]["key_count"])


if __name__ == "__main__":
    unittest.main()
