"use strict";

// Loaded by action.yml through actions/github-script; tests/js/ drives it with stubs.

const fs = require("fs");

const MARKER = "<!-- wouldrun -->"; // must match MARKER in wouldrun/pr_format.py
const ACTIONS_BOT = "github-actions[bot]";

async function ownLogins(github) {
  const logins = new Set([ACTIONS_BOT]);
  try {
    const { data } = await github.rest.users.getAuthenticated();
    if (data && data.login) {
      logins.add(data.login);
    }
  } catch (err) {
    // GITHUB_TOKEN cannot read GET /user; the bot login above covers it.
  }
  return logins;
}

function isOurs(comment, logins) {
  return (
    typeof comment.body === "string" &&
    comment.body.startsWith(MARKER) &&
    Boolean(comment.user) &&
    logins.has(comment.user.login)
  );
}

async function postStickyComment({ github, context, core, body, summaryPath }) {
  const { owner, repo } = context.repo;
  const issue_number = context.payload.pull_request.number;
  const logins = await ownLogins(github);
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner,
    repo,
    issue_number,
  });
  const existing = comments.find((c) => isOurs(c, logins));

  try {
    if (existing) {
      await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body });
      return "updated";
    }
    await github.rest.issues.createComment({ owner, repo, issue_number, body });
    return "created";
  } catch (err) {
    if (!err || err.status !== 403) {
      throw err;
    }
    core.warning(
      "wouldrun could not write its PR comment (HTTP 403; a pull_request run from a " +
        "fork gets a read-only token). The table is in the job summary instead."
    );
    fs.appendFileSync(summaryPath, body);
    return "summary";
  }
}

module.exports = { MARKER, postStickyComment };
