from __future__ import annotations

"""Key derivation for the `UserOwnedIndex` GSI shared by `dynamodb.py` and
`local_json.py` (kept as one function so both backends compute identical
keys from identical inputs -- the local-JSON `query_index` emulation is only
a faithful stand-in for the real DynamoDB Query if the two never drift).

A handful of entities are personal, user-owned resources (their own `user_id`
field, e.g. Commitment/IntegrationAccount) whose primary list access pattern
is "workspace_id + user_id". Reading the whole workspace partition for that
entity and filtering `user_id` in Python (the plain `Repository.list()` path)
means every request pays for every *other* user's rows too -- fine for the
local JSON backend / small dev data, not for a real DynamoDB deployment. This
index exists to make that access pattern an actual Query, not a Scan.

USER_OWNED_ENTITIES is intentionally small: it's the source of truth for
which entities get GSI attributes written and may use `Repository.
list_user_owned`. Action/Approval/AgentRun are deliberately not included --
see the README's "DynamoDB access patterns" section for why.
"""

USER_OWNED_INDEX = "UserOwnedIndex"
USER_OWNED_ENTITIES = frozenset({"commitment", "integration_account"})


def user_owned_index_keys(entity: str, workspace_id: str, user_id: str, created_at: str, item_id: str) -> tuple[str, str]:
    """GSI1PK/GSI1SK for one row of a user-owned entity.

    GSI1SK embeds the entity type and `created_at` (an `iso_now()` UTC
    timestamp, lexicographically sortable) ahead of the id, so a Query
    against this index can be narrowed to one entity type via a
    `sort_key_prefix` (`"COMMITMENT#"`) and comes back in chronological
    order for free -- no separate sort step, no Scan.
    """
    return f"WORKSPACE#{workspace_id}#USER#{user_id}", f"{entity.upper()}#{created_at}#{item_id}"
