When current library or API docs are needed, use the Chub CLI first.

Preferred workflow:
1. Refresh the local registry first: `chub update`
2. Search for the relevant doc or skill: `chub search <query>`
3. Fetch the exact entry before writing code:
   - `chub get <doc-id>`
   - `chub get <doc-id> --lang py`
   - `chub get <doc-id> --lang js`
4. If the work reveals a non-obvious project or environment gotcha, save it locally with `chub annotate`
5. If the doc quality is clearly good or bad, submit `chub feedback`

Core commands:
- `chub search [query]`
  Search docs and skills. With no query, list all available entries.
- `chub get <id> [--lang py|js]`
  Fetch docs or skills by ID.
- `chub annotate <id> <note>`
  Attach a local note to a doc or skill.
- `chub annotate <id> --clear`
  Remove the local annotation for that entry.
- `chub annotate --list`
  List all saved local annotations.
- `chub feedback <id> <up|down>`
  Submit a quality vote for a doc or skill.
- `chub feedback --status`
  Check whether feedback telemetry is enabled.

Annotation rules:
- Use annotations for knowledge that is not already obvious in the doc.
- Good annotation examples:
  - environment-specific gotchas
  - version-specific quirks
  - project-specific usage decisions
  - error resolutions discovered during implementation
- Do not annotate facts that are already clearly stated in the fetched doc.
- Annotations are local to this machine and persist across sessions.
- When a doc has an annotation, future `chub get` calls will include it automatically.

Feedback rules:
- Use feedback for the overall usefulness or correctness of the doc, not for local project notes.
- Positive labels to use when appropriate:
  - `accurate`
  - `well-structured`
  - `helpful`
  - `good-examples`
- Negative labels to use when appropriate:
  - `outdated`
  - `inaccurate`
  - `incomplete`
  - `wrong-examples`
  - `wrong-version`
  - `poorly-structured`
- If telemetry is disabled, `chub feedback` may be skipped silently. Check with `chub feedback --status`.

Examples:
- `chub search react router`
- `chub get react/router --lang js`
- `chub annotate stripe/api "Webhook verification requires raw body before signature validation"`
- `chub annotate --list`
- `chub feedback stripe/api up`
- `chub feedback openai/chat down --label outdated --label wrong-examples`

Prefer Chub over guessing or relying on stale docs. If Chub does not have the library or skill, fall back to the next best source.
