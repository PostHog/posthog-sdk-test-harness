"""Local-definition and request-scoped cache preparation with native attribution."""

from .cached_flag_steps import STEPS as CACHED_STEPS
from .contracts import decode_json, json_equal, require
from .flag_fixtures import FlagStateControls
from .flag_steps import evaluate, flag_table, network, snapshot_call, snapshot_keys, start_network_window, state
from .steps import Registry, expect, table

STEPS = Registry()
STEPS.definitions.extend(CACHED_STEPS.definitions)
STEPS.requirements.update(CACHED_STEPS.requirements)
DEFINITIONS = ("flags.definitions.install.v1",)
ACTIVITY = ("flags.evaluation_activity.v1",)


def constant_definition(key, value, number):
    require(type(value) is bool or isinstance(value, str), "invalid_step_data", "Expected boolean or variant")
    filters = {"groups": [{"properties": [], "rollout_percentage": 100}]}
    if isinstance(value, str):
        filters["multivariate"] = {"variants": [{"key": value, "rollout_percentage": 100}]}
        filters["groups"][0]["variant"] = value
    return {"id": number, "key": key, "active": value is not False, "version": 1, "filters": filters}


async def install(ctx):
    start_network_window(ctx)
    definitions = {"flags": list(state(ctx).definitions.values()), "cohorts": {}, "group_type_mapping": {}}
    await FlagStateControls(ctx).command("definitions_install", definitions=definitions)


async def add_definition(ctx, key, value):
    definitions = state(ctx).definitions
    number = (
        definitions[key]["id"] if key in definitions else max((d["id"] for d in definitions.values()), default=0) + 1
    )
    definitions[key] = constant_definition(key, value, number)
    await install(ctx)


@STEPS.step(
    r'local feature flag definitions resolve "([^\"]*)" for distinct id "([^\"]*)" as (.+)', fixtures=DEFINITIONS
)
async def definition_value(ctx, step, key, identity, value):
    # A constant rule establishes the stated value for this identity; it does not
    # claim identity-specific matching or install a table of evaluator answers.
    await add_definition(ctx, key, decode_json(value))


@STEPS.step(r'local feature flag definitions resolve for distinct id "([^\"]*)":', "dataTable", fixtures=DEFINITIONS)
async def definition_values(ctx, step, identity):
    values, _ = flag_table(step)
    for key, value in values.items():
        await add_definition(ctx, key, value)


@STEPS.step(
    r'local feature flag definitions include a flag "([^\"]*)" rolled out to distinct id "([^\"]*)"',
    fixtures=DEFINITIONS,
)
async def definition_rollout(ctx, step, key, identity):
    await add_definition(ctx, key, True)


@STEPS.step("local feature flag definitions include flags:", "dataTable", fixtures=DEFINITIONS)
async def definition_bulk(ctx, step):
    rows = table(step, {"key": "string", "value_for_user_123": "string"})
    require(len({r["key"] for r in rows}) == len(rows), "invalid_step_data", "Duplicate flag key")
    for row in rows:
        value = {"true": True, "false": False}.get(row["value_for_user_123"], row["value_for_user_123"])
        await add_definition(ctx, row["key"], value)


@STEPS.step(r'no local feature flag definition is loaded for "([^\"]*)"', fixtures=DEFINITIONS)
async def absent_definition(ctx, step, key):
    state(ctx).definitions.pop(key, None)
    await install(ctx)


@STEPS.step(
    r'local feature flag definitions cannot resolve "([^\"]*)" for distinct id "([^\"]*)"', fixtures=DEFINITIONS
)
async def inconclusive_definition(ctx, step, key, identity):
    definitions = state(ctx).definitions
    number = max((d["id"] for d in definitions.values()), default=0) + 1
    definition = constant_definition(key, True, number)
    definition["filters"]["groups"][0]["properties"] = [
        {"key": "fixture_required_property", "type": "person", "operator": "exact", "value": "available"}
    ]
    definitions[key] = definition
    await install(ctx)


@STEPS.step(
    r'cached feature flag evaluation for distinct id "([^\"]*)" contains:',
    "dataTable",
    fixtures=("flags.evaluation_cache.put.v1",),
)
async def evaluation_cache(ctx, step, identity):
    start_network_window(ctx)
    flags, payloads = flag_table(step)
    await FlagStateControls(ctx).command("evaluation_cache_put", distinct_id=identity, flags=flags, payloads=payloads)


@STEPS.step(
    r'evaluate flags is called for distinct id "([^\"]*)" with an empty flag key list',
    routes=("/evaluate_flags",),
    fixtures=ACTIVITY,
)
async def empty_evaluation(ctx, step, identity):
    state(ctx).activity_before = await FlagStateControls(ctx).command("evaluation_activity")
    await evaluate(ctx, {"distinct_id": identity, "flag_keys": []})


@STEPS.step(
    r'evaluate flags is called for distinct id "([^\"]*)" with local-only evaluation enabled',
    routes=("/evaluate_flags",),
)
async def local_only(ctx, step, identity):
    await evaluate(ctx, {"distinct_id": identity, "only_evaluate_locally": True})


@STEPS.step("the returned evaluation snapshot should be empty", routes=("/snapshot/keys",))
async def empty_snapshot(ctx, step):
    expect(await snapshot_keys(ctx) == [], "snapshot_keys", "Expected an empty evaluation snapshot")


async def no_activity(ctx, key):
    before = state(ctx).activity_before
    require(before is not None, "invalid_state", "No evaluation activity baseline")
    after = await FlagStateControls(ctx).command("evaluation_activity")
    require(after["implementation"] == before["implementation"], "invalid_response", "Activity component changed")
    require(
        all(after[k] >= before[k] for k in ("cache_lookups", "local_evaluations")),
        "invalid_response",
        "Activity counters moved backwards",
    )
    expect(after[key] == before[key], "unexpected_flag_activity", "Empty key scope consulted an evaluation component")


@STEPS.step("no cached feature flag evaluation result should have been consulted", fixtures=ACTIVITY)
async def no_cache_lookup(ctx, step):
    await no_activity(ctx, "cache_lookups")


@STEPS.step("no local feature flag evaluation should have been attempted", fixtures=ACTIVITY)
async def no_local_evaluation(ctx, step):
    await no_activity(ctx, "local_evaluations")


@STEPS.step("no remote feature flag evaluation request should have been sent")
async def no_remote(ctx, step):
    expect(not network(ctx), "flag_request_count", "Unexpected remote evaluation")


@STEPS.step(r'the snapshot should contain "([^\"]*)" with value (.+)', routes=("/snapshot/get_flag",))
async def snapshot_value(ctx, step, key, value):
    actual = await snapshot_call(ctx, "get_flag", {"key": key})
    expect(json_equal(actual, decode_json(value)), "flag_value", "Snapshot value differs")


@STEPS.step("the next remote feature flag evaluation request fails")
async def fail_evaluation(ctx, step):
    start_network_window(ctx)
    ctx.server.fail_next_flags(503)
    state(ctx).expected_failure_status = 503
