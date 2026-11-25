---
pypi/posthog-sdk-test-harness: minor
---

Add opt-in `unusable_group_key` cases to the `feature_flags_known_absent` suite. Adapters opt in with `feature_flags_unusable_group_key_v1`. The cases check that a group flag evaluated with the group omitted or with an empty string group key is `false` locally with no `/flags` request, in local-only mode and through the default getter. They also check that a person condition still decides a flag whose group condition has an empty string key, and that a dependency on a group flag with an empty string key is `false`. Adapters that also declare `feature_flags_json_group_keys_v1` run the group flag case with JSON `null`, `true`, `[]` and `{}` group keys. The `/get_feature_flag` request shape doesn't change.
