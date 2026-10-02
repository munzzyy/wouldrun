"use strict";

const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { MARKER, postStickyComment } = require("../../scripts/sticky-comment.js");

const BODY = `${MARKER}\n**wouldrun**: event \`pull_request\`, 1 workflow(s), 1 would fire\n`;

function stubs({ comments = [], login = null, createError = null, updateError = null } = {}) {
  const calls = { create: [], update: [], warnings: [] };
  const github = {
    paginate: async (fn, params) => {
      assert.strictEqual(fn, github.rest.issues.listComments);
      assert.deepStrictEqual(params, { owner: "o", repo: "r", issue_number: 7 });
      return comments;
    },
    rest: {
      users: {
        getAuthenticated: async () => {
          if (!login) {
            const err = new Error("Resource not accessible by integration");
            err.status = 403;
            throw err;
          }
          return { data: { login } };
        },
      },
      issues: {
        listComments: () => {},
        createComment: async (args) => {
          if (createError) throw createError;
          calls.create.push(args);
        },
        updateComment: async (args) => {
          if (updateError) throw updateError;
          calls.update.push(args);
        },
      },
    },
  };
  const context = { repo: { owner: "o", repo: "r" }, payload: { pull_request: { number: 7 } } };
  const core = { warning: (msg) => calls.warnings.push(msg) };
  const summaryPath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "wouldrun-js-")), "summary.md");
  return { github, context, core, summaryPath, calls };
}

function httpError(status) {
  const err = new Error(`HTTP ${status}`);
  err.status = status;
  return err;
}

test("creates a comment when there is none", async () => {
  const s = stubs();
  assert.strictEqual(await postStickyComment({ ...s, body: BODY }), "created");
  assert.deepStrictEqual(s.calls.create, [{ owner: "o", repo: "r", issue_number: 7, body: BODY }]);
  assert.deepStrictEqual(s.calls.update, []);
});

test("updates the comment the Actions bot wrote", async () => {
  const s = stubs({
    comments: [
      { id: 1, body: "looks good", user: { login: "alice" } },
      { id: 2, body: `${MARKER}\nold table`, user: { login: "github-actions[bot]" } },
    ],
  });
  assert.strictEqual(await postStickyComment({ ...s, body: BODY }), "updated");
  assert.deepStrictEqual(s.calls.update, [{ owner: "o", repo: "r", comment_id: 2, body: BODY }]);
  assert.deepStrictEqual(s.calls.create, []);
});

test("updates the comment a custom token's own account wrote", async () => {
  const s = stubs({
    login: "release-bot",
    comments: [{ id: 3, body: `${MARKER}\nold`, user: { login: "release-bot" } }],
  });
  assert.strictEqual(await postStickyComment({ ...s, body: BODY }), "updated");
  assert.strictEqual(s.calls.update[0].comment_id, 3);
});

test("ignores a comment someone else wrote with the marker in it", async () => {
  const s = stubs({
    comments: [
      { id: 4, body: `${MARKER}\nhijack`, user: { login: "mallory" } },
      { id: 5, body: `quoting ${MARKER} back`, user: { login: "github-actions[bot]" } },
    ],
  });
  assert.strictEqual(await postStickyComment({ ...s, body: BODY }), "created");
  assert.deepStrictEqual(s.calls.update, []);
  assert.strictEqual(s.calls.create.length, 1);
});

test("falls back to the job summary on a 403", async () => {
  for (const which of ["create", "update"]) {
    const comments =
      which === "update" ? [{ id: 6, body: `${MARKER}\nold`, user: { login: "github-actions[bot]" } }] : [];
    const s = stubs({
      comments,
      createError: httpError(403),
      updateError: httpError(403),
    });
    fs.writeFileSync(s.summaryPath, "before\n");
    assert.strictEqual(await postStickyComment({ ...s, body: BODY }), "summary");
    assert.strictEqual(fs.readFileSync(s.summaryPath, "utf8"), `before\n${BODY}`);
    assert.strictEqual(s.calls.warnings.length, 1);
    assert.match(s.calls.warnings[0], /403/);
  }
});

test("other errors still fail the step", async () => {
  const s = stubs({ createError: httpError(500) });
  await assert.rejects(postStickyComment({ ...s, body: BODY }), /HTTP 500/);
  assert.deepStrictEqual(s.calls.warnings, []);
});

test("the marker matches the one pr_format.py writes", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "wouldrun", "pr_format.py"), "utf8");
  assert.ok(src.includes(`MARKER = "${MARKER}"`));
});
