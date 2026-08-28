# ki-icl

The company context layer: facts about KI group, fetchable by id through an MCP client.

**This is a proof of concept.**
It implements the read path from `context-layer-phase1-spec.md` end to end, minus Azure.
It is not production ready, and the content in `domains/` is placeholder text that no owner has reviewed.

## What it is for

Skills tell an assistant *how to perform a task*.
This tells it *what is true about the company*: methodologies, offerings, guidelines, policy.

One sentence: **make a markdown file in a git repository fetchable through the KI MCP, by id, with a version stamp.**

The property that matters is that a wrong answer is impossible.
A lookup is an exact dictionary hit or an honest `not_found`.
There is no similarity path, no closest match, and no fuzzy-matching code anywhere to be reached.

## Quick start

```bash
make install     # .venv + dependencies
make test        # 38 tests
make demo        # walk the acceptance demo end to end
make serve-http  # MCP server on http://127.0.0.1:8000/mcp
```

## Layout

```
domains/company/                    the content. One folder per domain, one per artifact.
  domain.yaml                       id + description
  expense-policy/                   an artifact; the folder IS the artifact
    artifact.yaml                   title, kind, description
    README.md                       required entry document
scripts/validate_context.py         the gate. Collects every failure, never stops at the first.
scripts/package_context.py          tar.gz + per-domain _manifest.json + per-artifact version_id
scripts/demo.py                     the acceptance demo, over a real MCP client
server/artifacts.py                 the read path. Lifts into ki-mcp unchanged.
server/mcp_server.py                throwaway harness. Replaced by ki-mcp's tool registry.
```

`make package` writes two directories, and the difference matters:

| Directory | Contents | Used by |
|---|---|---|
| `dist/context/` | exactly `context.tar.gz` and `manifest.json` | the upload step (`az storage blob upload-batch`) |
| `dist/staging/` | the servable tree, byte-identical to the extracted archive | local dev (`CONTEXT_ROOT`) |

## Adding an artifact

1. `mkdir domains/company/<kebab-case-id>/`
2. Write `artifact.yaml` with `title`, `kind` and `description`.
   The `description` is what an agent reads to decide whether to fetch, so write it as a "when to use" signal, not a label.
3. Write `README.md`. Supporting files may nest freely, and must be text.
4. `make validate`, then open a pull request.

Text only: `.md`, `.yaml`, `.yml`, `.json`, `.txt`, `.csv`, 1 MiB per file.
This is not a style preference.
Measured on the existing skills catalog, 147 markdown files are 1.73 MB while the binaries beside them are 15.32 MB.
Repository size risk is entirely a binaries risk, and the allow-list removes it.

## The three tools

| Tool | Returns |
|---|---|
| `list_domains()` | one row per domain, forever. The cold-start entry point. |
| `get_domain_manifest(domain)` | one row per artifact, with descriptions to choose from. No file bodies. |
| `get_artifact(domain, ids)` | full text. Accepts one id or a list; each is answered independently. |

Every answer carries an opaque `version_id`, taken from the last commit that touched that artifact's folder.
Compare it for equality to detect staleness.
Never parse it.

## Connecting a client

```bash
make serve-http
```

Then point an MCP client at `http://127.0.0.1:8000/mcp`. For stdio instead:

```json
{
  "mcpServers": {
    "ki-icl": {
      "command": "/absolute/path/to/ki-icl/.venv/bin/python",
      "args": ["/absolute/path/to/ki-icl/server/mcp_server.py"],
      "env": { "CONTEXT_ROOT": "/absolute/path/to/ki-icl/dist/staging" }
    }
  }
}
```

## What this POC leaves out

Everything here is deliberate, and each item is cheap to add once it is wanted.

| Not built | Why, and what it costs |
|---|---|
| Azure blob fetch, ETag guard, cache swap | Parametrizing `ki-mcp/server/utils/skills_source.py`, which already works in production. Nothing new to design. |
| `publish-context.yml` (upload + `/refresh`) | Blocked on a federated credential for this repo on the publisher Entra app. Copy `ki-dev-skills/.github/workflows/publish-skills.yml` and change four things. |
| Registration in ki-mcp | Move `server/artifacts.py` to `ki-mcp/server/utils/artifacts.py` and the three tool functions to `server/tools/artifact_tools.py`. |
| Ownership, `CODEOWNERS`, approval routing | Deferred by decision. `owner` is already served as `null` at both levels, so adding it is data, not a schema change. |
| Search, similarity, resolution from task context | Deferred on a stated trigger. Adding it would break the property in the first section. |
| Binary assets | See the text-only rule above. |

## Lifting the read path into ki-mcp

`server/artifacts.py` is written against the spec's section 7.3 signatures so it moves unchanged apart from two lines:

- `ARTIFACTS_ROOT` becomes `settings.artifacts_dir`.
- `_file_payload` is deleted in favour of `from utils.file_payload import file_payload`.

`visible_domains()` is a pass-through today and must stay the only way a read path resolves a domain.
It is the seam per-identity scoping lands on, and its whole value is that no read path can be written that forgets it.
