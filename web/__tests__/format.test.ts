import { describe, it, expect } from "vitest";
import { getActionTimestamp, sortActionsNewestFirst, sortApprovalsNewestFirst } from "../lib/format";

describe("getActionTimestamp", () => {
  it("prefers executed_at over updated_at and created_at", () => {
    const action = {
      executed_at: "2026-10-20T05:00:00Z",
      updated_at: "2026-10-20T04:00:00Z",
      created_at: "2026-10-20T03:00:00Z",
    };
    expect(getActionTimestamp(action)).toBe(new Date("2026-10-20T05:00:00Z").getTime());
  });

  it("prefers updated_at over created_at when executed_at is missing", () => {
    const action = {
      executed_at: null,
      updated_at: "2026-10-20T04:30:00Z",
      created_at: "2026-10-20T02:00:00Z",
    };
    expect(getActionTimestamp(action)).toBe(new Date("2026-10-20T04:30:00Z").getTime());
  });

  it("falls back to created_at when executed_at and updated_at are missing", () => {
    const action = {
      created_at: "2026-10-20T01:15:00Z",
    };
    expect(getActionTimestamp(action)).toBe(new Date("2026-10-20T01:15:00Z").getTime());
  });

  it("returns 0 safely when all timestamp fields are missing or null", () => {
    expect(getActionTimestamp({})).toBe(0);
    expect(getActionTimestamp({ executed_at: null, updated_at: null, created_at: null })).toBe(0);
  });

  it("returns 0 safely when timestamp is an invalid date string", () => {
    expect(getActionTimestamp({ created_at: "not-a-valid-date" })).toBe(0);
  });
});

describe("sortActionsNewestFirst", () => {
  it("sorts newest actions first according to the best timestamp", () => {
    const actions = [
      { id: "act_1", created_at: "2026-10-20T01:00:00Z" },
      { id: "act_2", created_at: "2026-10-20T01:00:00Z", executed_at: "2026-10-20T04:00:00Z" },
      { id: "act_3", created_at: "2026-10-20T01:00:00Z", updated_at: "2026-10-20T02:30:00Z" },
    ];

    const sorted = sortActionsNewestFirst(actions);
    expect(sorted.map((a) => a.id)).toEqual(["act_2", "act_3", "act_1"]);
  });

  it("places actions with missing timestamps at the end", () => {
    const actions = [
      { id: "act_no_time", created_at: null },
      { id: "act_with_time", created_at: "2026-10-20T03:00:00Z" },
    ];

    const sorted = sortActionsNewestFirst(actions);
    expect(sorted.map((a) => a.id)).toEqual(["act_with_time", "act_no_time"]);
  });

  it("provides deterministic sorting by id when timestamps are identical", () => {
    const actions = [
      { id: "act_alpha", created_at: "2026-10-20T02:00:00Z" },
      { id: "act_zebra", created_at: "2026-10-20T02:00:00Z" },
      { id: "act_beta", created_at: "2026-10-20T02:00:00Z" },
    ];

    const sorted = sortActionsNewestFirst(actions);
    // "act_zebra", "act_beta", "act_alpha" (descending id)
    expect(sorted.map((a) => a.id)).toEqual(["act_zebra", "act_beta", "act_alpha"]);
  });

  it("does not mutate the original array", () => {
    const actions = [
      { id: "act_1", created_at: "2026-10-20T01:00:00Z" },
      { id: "act_2", created_at: "2026-10-20T02:00:00Z" },
    ];
    const copy = [...actions];
    sortActionsNewestFirst(actions);
    expect(actions).toEqual(copy);
  });
});

describe("sortApprovalsNewestFirst", () => {
  it("sorts approvals with newest requested_at first", () => {
    const approvals = [
      { id: "apr_old", requested_at: "2026-10-22T10:00:00Z" },
      { id: "apr_newest", requested_at: "2026-10-23T14:00:00Z" },
      { id: "apr_mid", requested_at: "2026-10-23T08:00:00Z" },
    ];

    const sorted = sortApprovalsNewestFirst(approvals);
    expect(sorted.map((a) => a.id)).toEqual(["apr_newest", "apr_mid", "apr_old"]);
  });

  it("provides deterministic sorting by id when requested_at are identical", () => {
    const approvals = [
      { id: "apr_alpha", requested_at: "2026-10-23T10:00:00Z" },
      { id: "apr_zebra", requested_at: "2026-10-23T10:00:00Z" },
      { id: "apr_beta", requested_at: "2026-10-23T10:00:00Z" },
    ];

    const sorted = sortApprovalsNewestFirst(approvals);
    expect(sorted.map((a) => a.id)).toEqual(["apr_zebra", "apr_beta", "apr_alpha"]);
  });

  it("does not mutate the original array", () => {
    const approvals = [
      { id: "apr_1", requested_at: "2026-10-22T10:00:00Z" },
      { id: "apr_2", requested_at: "2026-10-23T10:00:00Z" },
    ];
    const copy = [...approvals];
    sortApprovalsNewestFirst(approvals);
    expect(approvals).toEqual(copy);
  });
});
