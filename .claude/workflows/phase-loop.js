export const meta = {
  name: 'phase-loop',
  description: 'One delivery phase: optional design-review gate, plan with review loop, TDD build with per-package review loops, e2e QA loop, first-time-user UX walkthrough, UAT hand-off',
  whenToUse: 'Run with args {phase: "P1"} (and skipDesignReview: true after P0) to deliver the next phase of docs/playground-spec.md section 9. Each stage loops until an independent reviewer approves or the round limit is hit.',
  phases: [
    { title: 'Design review', detail: 'independent reviewer approves the design and spec, or the run stops' },
    { title: 'Plan', detail: 'implementation plan, reviewed by a separate agent until approved' },
    { title: 'Build', detail: 'one TDD implementer per work package, each followed by a review-and-fix loop' },
    { title: 'QA', detail: 'end-to-end QA against the plan scenario, with a fix loop' },
    { title: 'UX walkthrough', detail: 'a first-time-user review of the built screens and the wireframes; its backlog feeds the next plan' },
    { title: 'Hand-off', detail: 'UAT script and phase report for the user' },
  ],
}

const A = Object.assign({
  phase: 'P1',
  skipDesignReview: true,
  reviewWireframes: true,
  maxReviewRounds: 3,
  maxDesignRounds: 2,
  branch: 'claude/stock-trading-bot-research-c5ygze',
  repo: '/home/user/Test',
}, args || {})
const PH = A.phase
const TRAILER = 'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_012fhSuvhKRC2NqnCip2FWpA'

const CONTEXT = `
## Shared context (read fully)
Repository: ${A.repo} (git). Work ONLY on branch ${A.branch}. Never open pull requests, never rewrite history, never force-push, never run destructive git commands. Commit your own work when told to, and after EACH commit push this branch with "git push -u origin ${A.branch}" (on a network error retry up to 4 times, waiting 2, 4, 8 and 16 seconds); the container can restart and lose unpushed work.
Product documents: docs/playground-spec.md (the build contract; section 9 is the phase plan, this run is phase ${PH}), docs/playground-design.md (rationale), docs/wireframes/README.md (screens), docs/bot-performance-evidence.md, docs/implementation-plan.md, docs/trading-bot-research.md. Earlier phases left docs/plans/<phase>-implementation-plan.md, docs/plans/<phase>-report.md, docs/uat/<phase>-uat.md and docs/ux/<phase>-ux-review.md; read the most recent of each before planning or reviewing.
Environment facts, verified: Python 3.11 is the default python3 and Python 3.13 is at /usr/bin/python3.13; uv is installed (the project pins its interpreter; use "uv sync"); PyPI and the npm registry are reachable; Node 22 and npm are installed; Playwright browsers are pre-installed under /opt/pw-browsers (Chromium at /opt/pw-browsers/chromium-1194/chrome-linux/chrome; PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 is set; never run "playwright install"; pass executablePath when a pinned package version differs). github.com is reachable. Market data and news hosts are NOT reachable from this container (the proxy answers 403), so every external data client is written against recorded fixture files with an injectable HTTP layer; nothing in the test suite may touch the network; live calls are exercised by the user on their Mac at UAT. Machine: 4 CPUs, 15 GB RAM.
Product rules that always hold: the app never places, routes or automates real orders; it holds no broker credentials; the real portfolio is read-only, imported from Trade Republic exports (CSV and PDF); reporting currency is EUR; ISIN is the instrument key; cost basis uses FIFO lots (German tax); nothing user-facing may call anything "live trading".
Style for anything user-facing (docs, UI text, CLI messages, error messages): plain English, short sentences, no em-dashes, define a term the first time it appears.
Commits: small and descriptive with an imperative subject. End every commit message with exactly these two trailer lines:
${TRAILER}
`

const DESIGN_SCHEMA = { type: 'object', required: ['verdict', 'summary', 'findings', 'conditions_for_phase'], properties: {
  verdict: { type: 'string', enum: ['approve', 'approve_with_conditions', 'reject'] },
  summary: { type: 'string' },
  findings: { type: 'array', items: { type: 'object', required: ['severity', 'area', 'description', 'fixable_in_spec'], properties: {
    severity: { type: 'string', enum: ['blocking', 'major', 'minor'] }, area: { type: 'string' }, description: { type: 'string' }, fixable_in_spec: { type: 'boolean' }, suggestion: { type: 'string' } } } },
  conditions_for_phase: { type: 'array', items: { type: 'string' } } } }
const EDIT_SCHEMA = { type: 'object', required: ['changed', 'skipped', 'commit'], properties: { changed: { type: 'array', items: { type: 'string' } }, skipped: { type: 'array', items: { type: 'string' } }, commit: { type: 'string' } } }
const PLAN_SCHEMA = { type: 'object', required: ['planPath', 'summary', 'workPackages', 'e2eCommands', 'commit'], properties: {
  planPath: { type: 'string' }, summary: { type: 'string' },
  workPackages: { type: 'array', items: { type: 'object', required: ['id', 'title', 'goal', 'complexity', 'dependsOn'], properties: {
    id: { type: 'string' }, title: { type: 'string' }, goal: { type: 'string' }, complexity: { type: 'string', enum: ['low', 'medium', 'high'] }, dependsOn: { type: 'array', items: { type: 'string' } } } } },
  e2eCommands: { type: 'array', items: { type: 'string' } }, commit: { type: 'string' } } }
const REVIEW_SCHEMA = { type: 'object', required: ['approved', 'summary', 'blocking', 'suggestions'], properties: {
  approved: { type: 'boolean' }, summary: { type: 'string' },
  blocking: { type: 'array', items: { type: 'object', required: ['issue', 'fix'], properties: { issue: { type: 'string' }, fix: { type: 'string' }, file: { type: 'string' } } } },
  suggestions: { type: 'array', items: { type: 'string' } } } }
const WP_SCHEMA = { type: 'object', required: ['summary', 'commits', 'filesChanged', 'testCommand', 'allGreen', 'deviations'], properties: {
  summary: { type: 'string' }, commits: { type: 'array', items: { type: 'string' } }, filesChanged: { type: 'array', items: { type: 'string' } },
  testCommand: { type: 'string' }, allGreen: { type: 'boolean' }, deviations: { type: 'array', items: { type: 'string' } }, notes: { type: 'string' } } }
const QA_SCHEMA = { type: 'object', required: ['passed', 'summary', 'defects', 'testsRun'], properties: {
  passed: { type: 'boolean' }, summary: { type: 'string' }, testsRun: { type: 'string' },
  defects: { type: 'array', items: { type: 'object', required: ['severity', 'description', 'repro', 'suggestedFix'], properties: {
    severity: { type: 'string', enum: ['blocking', 'major', 'minor'] }, description: { type: 'string' }, repro: { type: 'string' }, suggestedFix: { type: 'string' } } } } } }
const UX_SCHEMA = { type: 'object', required: ['reportPath', 'headline', 'findingCounts', 'backlog', 'commit'], properties: {
  reportPath: { type: 'string' }, headline: { type: 'array', items: { type: 'string' } },
  findingCounts: { type: 'object', required: ['blocking', 'major', 'minor'], properties: { blocking: { type: 'integer' }, major: { type: 'integer' }, minor: { type: 'integer' } } },
  backlog: { type: 'array', items: { type: 'object', required: ['id', 'size', 'title'], properties: { id: { type: 'string' }, size: { type: 'string', enum: ['S', 'M', 'L'] }, title: { type: 'string' } } } },
  commit: { type: 'string' } } }
const HANDOFF_SCHEMA = { type: 'object', required: ['uatPath', 'reportPath', 'commit'], properties: { uatPath: { type: 'string' }, reportPath: { type: 'string' }, commit: { type: 'string' } } }

const fmtFindings = (fs) => fs.map((f, i) => `${i + 1}. [${f.severity}] ${f.area}: ${f.description}${f.suggestion ? ' Suggestion: ' + f.suggestion : ''}`).join('\n')
const fmtBlocking = (bs) => bs.map((b, i) => `${i + 1}. ${b.file ? b.file + ': ' : ''}${b.issue} -> fix: ${b.fix}`).join('\n')
const fmtWps = (wps) => wps.map(w => `- ${w.id} (${w.complexity}) ${w.title}: ${w.goal}${w.dependsOn.length ? ' [after ' + w.dependsOn.join(', ') + ']' : ''}`).join('\n')

// ---------- prompts ----------
const designReviewPrompt = (round) => `${CONTEXT}
## Your task: independent design review (round ${round})
You are an independent reviewer who has not seen this project before. Read docs/playground-spec.md in full, then docs/playground-design.md, docs/wireframes/README.md, docs/bot-performance-evidence.md and docs/implementation-plan.md, and the reports of earlier phases under docs/plans and docs/ux. Do not edit any file.
Review the overall design and the implementation spec before phase ${PH} starts. Judge:
1. Internal consistency between the spec, the design document and the wireframes (screens, phases, object model, API, cost and hours).
2. Whether the phase ${PH} row in spec section 9 is well defined, buildable and testable in this environment (see the environment facts).
3. Data model and API completeness for phase ${PH}.
4. Safety and legal posture: no real orders, no broker credentials, recommendations labelled, tax estimates described as estimates.
5. Realism of the free data sources and of a Trade Republic import without an API.
6. Risks the spec misses, and anything a builder would have to guess.
For every finding give severity (blocking, major, minor), the area, a description and a suggestion. Mark fixable_in_spec true when an editor could fix it by clarifying, completing or making documents consistent WITHOUT changing any row of the decision-record tables in spec section 0, the user's scope choices or their preferences; mark it false when it questions such a decision (those are reported to the user, not fixed).
Verdict: approve, approve_with_conditions, or reject. Reject only when blocking findings make phase ${PH} unbuildable or unsafe as specified. In conditions_for_phase list concrete constraints the phase plan must respect (at most ten, each one sentence).`

const specEditPrompt = (findings, round) => `${CONTEXT}
## Your task: apply design-review fixes to the documents (round ${round})
A design reviewer rejected the spec. Fix ONLY the findings below, all marked as fixable in the spec. Never change a row of the decision-record tables in docs/playground-spec.md section 0, the scope choices or the user's preferences; if a finding needs that, skip it and list it under skipped with the reason. Keep the documents' style. Add one short paragraph to the "What changed" notes near the top of docs/playground-spec.md describing the edits. Commit with the subject "Spec: address design review findings (round ${round})".
Findings:
${fmtFindings(findings)}`

const plannerPrompt = (conditions) => `${CONTEXT}
## Your task: write the implementation plan for phase ${PH}
Read docs/playground-spec.md in full (section 9 defines phase ${PH}; the other sections carry the details it points to), the previous phase's plan, report and UAT guide under docs/plans and docs/uat, and the most recent UX review under docs/ux (its backlog is ordered; items UX1 to UX5 go into this plan as work packages or inside the packages that touch the same screens, and the rest are listed in a "deferred UX items" section with a reason). Skim docs/playground-design.md and docs/wireframes/README.md for intent. Conditions from the design review that the plan must respect:
${conditions.length ? conditions.map((c, i) => `${i + 1}. ${c}`).join('\n') : '(none)'}
Write docs/plans/${PH}-implementation-plan.md with these sections:
1. Goal and acceptance criteria: quote the phase row from spec section 9 and derive testable acceptance criteria; state what is out of scope for this phase.
2. Repository layout and tooling: extend what earlier phases built (read the code); do not restructure without a reason stated in the plan.
3. Technology choices consistent with spec section 8 and the environment facts.
4. Data model for this phase: the tables and files from spec section 4 that this phase adds or changes, with types and a migration note.
5. Module-by-module design with responsibilities and interfaces (function signatures where it matters).
6. External data and documents: for each external source this phase touches, how the client is structured with an injectable HTTP or file layer, what fixture files are recorded, and how an unknown layout or an unavailable source is handled (never a silent failure).
7. Test plan: unit tests, property tests where useful, golden-file tests, contract tests for the API, component tests for the page, and an end-to-end scenario QA will execute with exact shell commands. No test may use the network.
8. UAT outline for the user (a Trade Republic customer on macOS, comfortable with a terminal).
9. Work packages: an ordered list of 6 to 12 packages. Each has id (WP1, WP2, ...), title, goal, files to create or change, tests to write first, dependencies, complexity (low = mechanical and fully specified, suitable for a small model; medium = ordinary implementation; high = design judgement or tricky logic), and a definition of done. The last package integrates the phase end to end. Order them so that each package only depends on earlier ones.
Commit the plan with the subject "Plan: phase ${PH} implementation plan". Return the structured summary; e2eCommands must be the exact commands of section 7's scenario in order.`

const planReviewPrompt = (plan, conditions, round) => `${CONTEXT}
## Your task: review the phase ${PH} implementation plan (round ${round})
You are a separate reviewer; you did not write the plan. Read ${plan.planPath}, docs/playground-spec.md (section 9 row for ${PH}, plus the sections it cites) and the most recent UX review under docs/ux. Conditions from the design review:
${conditions.length ? conditions.map((c, i) => `${i + 1}. ${c}`).join('\n') : '(none)'}
Check: completeness against the phase row and the success criteria in spec section 1; the first five UX backlog items are covered or explicitly deferred with a reason; every acceptance criterion has a test; packages are TDD-able and each is finishable by one agent in one sitting; dependency order is correct; the fixture strategy works with no external network; the end-to-end scenario has exact commands; nothing violates the product rules; the plan does not quietly widen the phase beyond the spec row. Do not edit any file.
Return approved=true only when nothing blocking remains. Blocking means: a missing acceptance criterion, an untestable package, a network dependency in tests, a rule violation, or an ordering error. Everything else is a suggestion.`

const planRevisePrompt = (plan, review, round) => `${CONTEXT}
## Your task: revise the phase ${PH} implementation plan (round ${round})
A reviewer found blocking issues in ${plan.planPath}. Fix every blocking issue below in the plan document; consider the suggestions and apply the ones that improve it without widening the phase. Keep the document structure. Commit with the subject "Plan: revise phase ${PH} plan (round ${round})". Return the updated structured summary (all work packages, in order).
Blocking:
${fmtBlocking(review.blocking)}
Suggestions:
${review.suggestions.map((s, i) => `${i + 1}. ${s}`).join('\n') || '(none)'}`

const implPrompt = (wp, plan) => `${CONTEXT}
## Your task: implement work package ${wp.id}, "${wp.title}"
Read ${plan.planPath} in full first, then the spec sections the package cites, then the existing code the package touches. The package goal: ${wp.goal}
All packages, for orientation (implement only yours):
${fmtWps(plan.workPackages)}
Work test-driven:
1. Write the tests listed for ${wp.id} in the plan (add more if the plan missed an edge case). Run them and confirm they fail for the right reason. Commit with subject "test(${wp.id}): ...".
2. Implement until those tests pass. Run the whole check (scripts/check.sh or the command the README names). Commit with subject "feat(${wp.id}): ...". Use more than one commit if it helps.
Rules: do not implement other packages' scope; do not weaken or delete a test to make it pass (if a test is wrong, fix it and explain in deviations); no network access in code paths that tests exercise, use the injectable layer and fixtures; if the plan is impossible as written, implement the closest correct thing and record the deviation. Leave the working tree clean (everything committed and pushed).
Return the structured report; testCommand is the exact command that runs the full check; allGreen is true only if the whole check passes at your last commit.`

const codeReviewPrompt = (wp, report, plan, round) => `${CONTEXT}
## Your task: independent code review of work package ${wp.id}, "${wp.title}" (round ${round})
You did not write this code. Read the ${wp.id} section of ${plan.planPath}, then inspect the changes: the implementer reports commits ${JSON.stringify(report.commits)} and files ${JSON.stringify(report.filesChanged)}; use git log and git show or git diff on those commits and files. Run the full check yourself with: ${report.testCommand}. Implementer notes: ${report.notes || '(none)'}. Deviations declared: ${JSON.stringify(report.deviations)}.
Check: correctness (money, FIFO, currency conversion, dates and time zones, parsing edge cases, duplicate detection, error handling); tests fail without the implementation and cover the edge cases the plan lists; no network in tests; product rules and plain-language messages; the package does what the plan says and nothing outside it. Do not edit any file.
Return approved=true only when nothing blocking remains. Blocking means wrong behaviour, a missing required test, a failing check, a rule violation, or an undeclared deviation from the plan. Style and naming are suggestions.`

const fixPrompt = (wp, review, report, round) => `${CONTEXT}
## Your task: fix review findings on work package ${wp.id}, "${wp.title}" (round ${round})
A reviewer found blocking issues. Fix each one, test-first where a test is missing, keep the whole check green (${report.testCommand}), and commit with subject "fix(${wp.id}): ...". Do not widen the package. Leave the working tree clean.
Blocking:
${fmtBlocking(review.blocking)}
Suggestions (apply only if cheap and safe):
${review.suggestions.map((s, i) => `${i + 1}. ${s}`).join('\n') || '(none)'}
Return the structured report for your fixes (commits, files, testCommand, allGreen, deviations).`

const qaPrompt = (plan, built, round) => `${CONTEXT}
## Your task: end-to-end QA for phase ${PH} (round ${round})
Read ${plan.planPath} (sections 1, 7 and 9). Package status from the build:
${built.map(b => `- ${b.id} ${b.title}: ${b.failed ? 'IMPLEMENTER FAILED' : (b.approved ? 'approved' : 'NOT approved') + ' after ' + b.rounds + ' review round(s), check green: ' + b.allGreen}`).join('\n')}
Do, in order: (1) make a fresh clone of the branch in a temporary folder, create the environment as the plan says and run the full check there; (2) execute the end-to-end scenario exactly with these commands, in order:
${plan.e2eCommands.map((c, i) => `${i + 1}. ${c}`).join('\n')}
(3) probe the edges: malformed input, a duplicate re-import, a missing mapping or an unavailable source, an unknown layout (must land in the review queue, never a crash or silent skip); (4) if the phase has web pages, start the server, drive them with Playwright (headless) through the phase's flows at 1280 and 390 pixels wide and save screenshots under docs/uat/screenshots/${PH}/; (5) check that every acceptance criterion in plan section 1 is met and that user-facing messages are plain English.
Record every defect with severity, exact repro steps and a suggested fix. Do not fix application code. You may add a failing test that demonstrates a defect and commit it with subject "test(qa): ...". Commit screenshots if you took any.
passed=true only if the scenario completes with correct results and there are no blocking or major defects.`

const qaFixPrompt = (qa, plan, round) => `${CONTEXT}
## Your task: fix defects found by end-to-end QA (round ${round})
Read ${plan.planPath}. QA found these defects:
${qa.defects.map((d, i) => `${i + 1}. [${d.severity}] ${d.description}\n   Repro: ${d.repro}\n   Suggested fix: ${d.suggestedFix}`).join('\n')}
Fix all blocking and major defects (minor ones if cheap), test-first, keeping any test QA added. Keep the whole check green. Commit with subject "fix(qa): ...". Leave the working tree clean. Return the structured report.`

const uxPrompt = (plan) => `${CONTEXT}
## Your task: first-time-user UX and UI walkthrough for phase ${PH}
You are a UX and UI reviewer playing a first-time user: a Germany-based Trade Republic customer who has just installed the app, knows nothing about it and has their broker files. Do not edit application code or tests; you only add your report and screenshots. Run the app from a data folder of your own outside the repository and on an unusual port.
Read docs/uat/${PH}-uat.md if it exists, else the previous phase's UAT guide, ${plan.planPath} sections 1 and 7, and the previous UX review under docs/ux (so you can say which of its backlog items are now done and which problems remain).
Part 1, the built app: set up, build and start it; open it in headless Chromium at 1280 and 390 pixels wide; note your first impression in one sentence; then try to reach the phase's goal as the persona would, using the fixtures as your files; also do what a first-timer does: upload the wrong file, repeat an action, reload mid-way, look for help and for "what should I do next". Screenshot every step, error, empty and loading state at both widths under docs/ux/screenshots/${PH}/ with numbered, descriptive names. For each step record what you expected, what you saw, where you hesitated, what wording or layout caused it, and how long it took to know what to do next.
${A.reviewWireframes ? 'Part 2, the wireframes: render each docs/wireframes/project/*.dc.html board in Chromium at 1280 wide (file:// URL; each has a fixed-size root element), screenshot it, and judge it as the first-time user: is the purpose clear in three seconds, what is the one thing to do here, how many modules compete for attention, smallest type size and longest line, what could move to a second level, where numbers lack a sentence that says what they mean, what you would remove. Skip boards whose screens this phase has now built, and judge the built screens instead.' : 'Part 2 is skipped for this phase: judge only the built screens.'}
Part 3, write docs/ux/${PH}-ux-review.md, plain English, short sentences, no em-dashes, under about 4,000 words, with these sections: 1. Summary: the five changes that would help a first-time user most, one paragraph each with the screenshot that shows the problem. 2. The walkthrough narrative with screenshot references, then a findings table (severity blocking, major, minor; where; what happened; why it confused you; recommendation). 3. First-time guidance design: welcome state, guided first steps with progress, what the app says after each step, empty states that teach, where "what next" lives, when the tour appears, how help returns; with the exact wording you propose. 4. Visual and layout system: spacing, type scale with a minimum size, line length, density (modules per screen, what a card may contain), tables versus sentences, colour and emphasis, charts (fewer, larger, one message each), phone layout; before and after for at least two screens. 5. Information architecture: what each main screen shows by default and what moves to a second level; a calm default and an expert density switch if you think it right. 6. Board-by-board notes (built screens and wireframes). 7. Backlog for the next phase: prioritised items UX1, UX2, ... each with size (S, M, L), the screens it touches and its acceptance test, ordered so the first five can go into the next plan as they are; mark items carried over from the previous review. 8. Appendix: screenshot list.
Stop any server you started. Commit the report and screenshots with the subject "UX: phase ${PH} first-time-user walkthrough". Return the structured summary.`

const handoffPrompt = (plan, built, qa, ux, design, planRounds) => `${CONTEXT}
## Your task: write the UAT script and the phase report for phase ${PH}
Read ${plan.planPath}, README.md, the UX review at ${ux ? ux.reportPath : 'docs/ux (none this phase)'} and the code as needed. Facts to report: design review verdict ${design ? design.verdict : 'skipped'}; plan approved after ${planRounds} review round(s); packages:
${built.map(b => `- ${b.id} ${b.title}: ${b.failed ? 'implementer failed' : (b.approved ? 'approved' : 'not approved') + ', ' + b.rounds + ' review round(s), check green: ' + b.allGreen + (b.deviations && b.deviations.length ? ', deviations: ' + b.deviations.join('; ') : '')}`).join('\n')}
QA: ${qa ? (qa.passed ? 'passed' : 'not passed') + '; ' + qa.summary + '; open defects: ' + JSON.stringify(qa.defects) : 'not run'}.
UX walkthrough: ${ux ? 'headline changes: ' + ux.headline.join(' | ') + '; findings: ' + JSON.stringify(ux.findingCounts) + '; backlog items: ' + ux.backlog.map(b => b.id + ' (' + b.size + ') ' + b.title).join('; ') : 'not run'}.
Write two documents and commit them with the subject "Docs: phase ${PH} UAT script and report":
1. docs/uat/${PH}-uat.md, for the user (a Germany-based Trade Republic customer on macOS, comfortable with a terminal, not a developer by trade): what this phase delivers; how to install or update; how to get their documents out of Trade Republic if this phase changed anything there; how to run each new feature; a numbered checklist of what to verify; known limitations; how to report a problem (which files to send, with personal data removed). Plain English.
2. docs/plans/${PH}-report.md: what was built, the packages with their review rounds, test counts (run the check to get them), QA results and open defects, the UX walkthrough's headline changes and its backlog, deviations from the plan, known limitations, and the recommended scope for the next phase from spec section 9 plus the UX backlog.
Also update the "Running the app" section of README.md if this phase changed how to run it.`

// ---------- run ----------
let design = null
if (!A.skipDesignReview) {
  phase('Design review')
  for (let r = 1; r <= A.maxDesignRounds; r++) {
    design = await agent(designReviewPrompt(r), { label: `design review r${r}`, phase: 'Design review', model: 'opus', effort: 'high', schema: DESIGN_SCHEMA })
    if (!design) return { phase: PH, stopped: 'design_review_agent_failed' }
    log(`Design review round ${r}: ${design.verdict}, ${design.findings.length} findings`)
    if (design.verdict !== 'reject') break
    const fixable = design.findings.filter(f => f.fixable_in_spec && f.severity !== 'minor')
    if (!fixable.length || r === A.maxDesignRounds) break
    const edit = await agent(specEditPrompt(fixable, r), { label: `spec edit r${r}`, phase: 'Design review', model: 'opus', schema: EDIT_SCHEMA })
    log(`Spec edited: ${edit ? edit.changed.length + ' changes, ' + edit.skipped.length + ' skipped' : 'editor failed'}`)
  }
  if (design.verdict === 'reject') return { phase: PH, stopped: 'design_review_rejected', design }
}

phase('Plan')
const conditions = design ? design.conditions_for_phase : []
let plan = await agent(plannerPrompt(conditions), { label: 'planner', phase: 'Plan', model: 'opus', effort: 'high', schema: PLAN_SCHEMA })
if (!plan) return { phase: PH, stopped: 'planner_failed', design }
let planReview = null
let planRounds = 0
for (let r = 1; r <= A.maxReviewRounds; r++) {
  planRounds = r
  planReview = await agent(planReviewPrompt(plan, conditions, r), { label: `plan review r${r}`, phase: 'Plan', model: 'opus', effort: 'high', schema: REVIEW_SCHEMA })
  if (!planReview) break
  log(`Plan review round ${r}: ${planReview.approved ? 'approved' : planReview.blocking.length + ' blocking issue(s)'}`)
  if (planReview.approved || r === A.maxReviewRounds) break
  const revised = await agent(planRevisePrompt(plan, planReview, r), { label: `plan revise r${r}`, phase: 'Plan', model: 'opus', schema: PLAN_SCHEMA })
  if (revised) plan = revised
}
if (!planReview || !planReview.approved) return { phase: PH, stopped: 'plan_not_approved', design, plan, planReview, planRounds }

phase('Build')
const MODEL = { low: 'haiku', medium: 'sonnet', high: 'opus' }
const FIXER = { low: 'sonnet', medium: 'sonnet', high: 'opus' }
const built = []
for (const wp of plan.workPackages) {
  log(`${wp.id} (${wp.complexity}): ${wp.title}`)
  let report = await agent(implPrompt(wp, plan), { label: `${wp.id} build`, phase: 'Build', model: MODEL[wp.complexity] || 'sonnet', schema: WP_SCHEMA })
  if (!report) { built.push({ id: wp.id, title: wp.title, complexity: wp.complexity, failed: true, approved: false, rounds: 0, allGreen: false, deviations: [] }); log(`${wp.id}: implementer failed`); continue }
  let review = null
  let rounds = 0
  const reviewerModel = wp.complexity === 'high' ? 'opus' : 'sonnet'
  for (let r = 1; r <= A.maxReviewRounds; r++) {
    rounds = r
    review = await agent(codeReviewPrompt(wp, report, plan, r), { label: `${wp.id} review r${r}`, phase: 'Build', model: reviewerModel, effort: 'high', schema: REVIEW_SCHEMA })
    if (!review) break
    log(`${wp.id} review round ${r}: ${review.approved ? 'approved' : review.blocking.length + ' blocking issue(s)'}`)
    if (review.approved || r === A.maxReviewRounds) break
    const fixed = await agent(fixPrompt(wp, review, report, r), { label: `${wp.id} fix r${r}`, phase: 'Build', model: FIXER[wp.complexity] || 'sonnet', schema: WP_SCHEMA })
    if (fixed) report = Object.assign({}, report, fixed, { commits: report.commits.concat(fixed.commits || []), filesChanged: Array.from(new Set(report.filesChanged.concat(fixed.filesChanged || []))), deviations: report.deviations.concat(fixed.deviations || []) })
  }
  built.push({ id: wp.id, title: wp.title, complexity: wp.complexity, failed: false, approved: !!(review && review.approved), rounds, allGreen: report.allGreen, deviations: report.deviations, lastReview: review })
}

phase('QA')
let qa = null
let qaRounds = 0
for (let r = 1; r <= A.maxReviewRounds; r++) {
  qaRounds = r
  qa = await agent(qaPrompt(plan, built, r), { label: `e2e qa r${r}`, phase: 'QA', model: 'opus', effort: 'high', schema: QA_SCHEMA })
  if (!qa) break
  log(`QA round ${r}: ${qa.passed ? 'passed' : qa.defects.length + ' defect(s)'}`)
  if (qa.passed || r === A.maxReviewRounds) break
  await agent(qaFixPrompt(qa, plan, r), { label: `qa fix r${r}`, phase: 'QA', model: 'opus', schema: WP_SCHEMA })
}

phase('UX walkthrough')
const ux = await agent(uxPrompt(plan), { label: 'first-time-user walkthrough', phase: 'UX walkthrough', model: 'opus', effort: 'high', schema: UX_SCHEMA })
if (ux) log(`UX walkthrough: ${ux.findingCounts.blocking} blocking, ${ux.findingCounts.major} major, ${ux.findingCounts.minor} minor; ${ux.backlog.length} backlog items`)

phase('Hand-off')
const handoff = await agent(handoffPrompt(plan, built, qa, ux, design, planRounds), { label: 'uat script and report', phase: 'Hand-off', model: 'sonnet', schema: HANDOFF_SCHEMA })

return {
  phase: PH,
  design,
  plan: { path: plan.planPath, summary: plan.summary, rounds: planRounds, approved: planReview.approved, workPackages: plan.workPackages.map(w => w.id + ' ' + w.title) },
  packages: built.map(b => ({ id: b.id, title: b.title, complexity: b.complexity, failed: b.failed, approved: b.approved, rounds: b.rounds, allGreen: b.allGreen, deviations: b.deviations, openBlocking: b.lastReview && !b.lastReview.approved ? b.lastReview.blocking : [] })),
  qa: { passed: qa ? qa.passed : null, rounds: qaRounds, summary: qa ? qa.summary : 'not run', defects: qa ? qa.defects : [] },
  ux: ux ? { reportPath: ux.reportPath, headline: ux.headline, findingCounts: ux.findingCounts, backlog: ux.backlog } : null,
  handoff,
}
