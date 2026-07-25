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

        self.store.assign_tokens(9, [7, 8], customer["id"])
        self.store.assign_tokens(9, [8], internal["id"])

        self.assertEqual({7: customer["id"], 8: internal["id"]}, self.store.membership_map(9))
        self.assertEqual([], self.store.list_groups(10))
        counts = {group["name"]: group["key_count"] for group in self.store.list_groups(9)}
        self.assertEqual({"客户项目": 1, "内部工具": 1}, counts)
        with self.assertRaisesRegex(KeyGroupError, "group not found"):
            self.store.assign_tokens(10, [7], customer["id"])

    def test_delete_group_keeps_keys_but_removes_their_monitor_group(self):
        group = self.store.create_group(9, "临时", "amber")
        self.store.assign_tokens(9, [7, 8], group["id"])

        deleted = self.store.delete_group(9, group["id"])

        self.assertEqual("临时", deleted["name"])
        self.assertEqual({}, self.store.membership_map(9))
        self.assertEqual([], self.store.list_groups(9))

    def test_group_can_be_renamed_and_deleted_keys_can_be_unassigned(self):
        group = self.store.create_group(9, "旧名称", "slate")
        self.store.assign_tokens(9, [7, 8], group["id"])

        updated = self.store.update_group(9, group["id"], "新名称", "emerald")
        self.store.remove_tokens(9, [7])

        self.assertEqual("新名称", updated["name"])
        self.assertEqual("emerald", updated["color"])
        self.assertEqual({8: group["id"]}, self.store.membership_map(9))

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
            memberships={8: 3, 9: 3, 99: 3},
            start_timestamp=100,
            end_timestamp=200,
        )

        self.assertEqual(10, result["summary"]["requests"])
        self.assertEqual(650, result["summary"]["quota"])
        self.assertEqual(2, result["token_usage"]["7"]["requests"])
        self.assertIsNone(result["token_usage"]["7"]["key_group_id"])
        self.assertEqual(8, result["groups"][0]["usage"]["requests"])
        self.assertEqual(550, result["groups"][0]["usage"]["quota"])
        self.assertEqual(2, result["groups"][0]["usage"]["models"])
        self.assertEqual(2, result["groups"][0]["key_count"])
        self.assertEqual(2, result["ungrouped"]["usage"]["requests"])
        self.assertEqual("current_membership", result["usage_attribution"])
        self.assertNotIn("use_group", result["groups"][0])
        self.assertNotIn("99", result["token_usage"])


if __name__ == "__main__":
    unittest.main()
