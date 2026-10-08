import type { Metadata } from 'next'
import { C, Code } from '../_ui'

export const metadata: Metadata = {
  title: 'Create a project',
  description: 'POST /projects: start from an outcome template, or author the task config yourself.',
  alternates: { canonical: 'https://senebiclabs.com/docs/create-a-project' },
  openGraph: { url: 'https://senebiclabs.com/docs/create-a-project' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-kicker">Endpoints</span>
          <h1>
            <span className="m m-post">POST</span>
            <span className="ep">/projects</span>
            <span className="tag">Create a project</span>
          </h1>
          <p>
            Create a project and get back a <C>project_id</C> to push items to.
          </p>

          <h3>Start from a template (recommended)</h3>
          <p>
            Pick what you want to achieve and we build the project for you — no config to author.
            List the outcomes with <C>GET /templates</C>:
          </p>
          <ul>
            <li><C>model_evaluation</C> — grade your model&rsquo;s outputs → accuracy + safety scorecard</li>
            <li><C>data_labeling</C> — your data back, labelled → labelled dataset + summary</li>
            <li><C>rlhf_preference</C> — pick the better of two responses → preference pairs for RLHF</li>
            <li><C>gold_answers</C> — write the ideal answer → gold dataset for fine-tuning</li>
            <li><C>case_review</C> — judge whether AI helped or hurt on full cases → audit dataset + impact distribution</li>
            <li><C>benchmark_creation</C> — author challenging test cases → an evaluation benchmark</li>
            <li><C>rubric_creation</C> — design the scorecard your model is graded against → a reusable grading rubric</li>
            <li><C>contradiction_creation</C> — plant a clinically important conflict between a record and a source → a contradiction test set with the expected safe behaviour</li>
            <li><C>adversarial_prompts</C> — write probes that expose model gaps → a red-teaming test set</li>
            <li><C>fact_checking</C> — highlight errors in an answer, rewrite it, cite a source → accuracy + a corrections dataset</li>
            <li><C>dialogue_creation</C> — author realistic patient-clinician dialogues → synthetic training data</li>
            <li><C>response_ranking</C> — rank two answers on accuracy/empathy/clarity/safety → preference pairs with per-axis scores</li>
            <li><C>clinical_safety_eval</C> — full safety review: correctness, triage, red flags, reasoning → safety scorecard with severity + failure modes</li>
            <li><C>triage_eval</C> — is the urgency right → triage accuracy split into under-triage, over-triage, missed emergencies</li>
            <li><C>reasoning_eval</C> — score the reasoning step by step → where in the reasoning it fails</li>
            <li><C>grounding_eval</C> — retrieval, grounding, citations, hallucination, conflicting evidence → RAG failure breakdown</li>
            <li><C>agent_trace_eval</C> — grade a multi-step agent&rsquo;s whole trajectory → where it first went wrong, which steps failed and how</li>
          </ul>
          <p>Create from one, supplying your own <C>classes</C> (label set) where it applies:</p>
          <Code>{`curl -X POST "$BASE/projects" \\
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \\
  -d '{
    "name": "Triage model eval",
    "template": "model_evaluation",
    "classes": ["Routine", "Urgent", "Emergency"],
    "webhook_url": "https://your-app.com/hooks/senebiclabs"
  }'`}</Code>
          <p>That is all most projects need. The rest of this section is the <b>advanced</b> path — authoring a full config yourself.</p>
          <p>
            <b>Second reading.</b> Authoring templates (<C>gold_answers</C>, <C>benchmark_creation</C>,{' '}
            <C>contradiction_creation</C>, <C>adversarial_prompts</C>, <C>dialogue_creation</C>) are
            written by one clinician, so a second, <b>different</b> clinician reads every item before
            it counts. The reader approves it, edits and approves it, or sends it back with a reason,
            and it is then written again. Only approved items are delivered. Each one carries{' '}
            <C>{`"second_reading": {"approved": true, "rounds": 1, "edited_by_reader": false, "by_senior_reviewer": false}`}</C>{' '}
            in your results, where <C>rounds</C> counts the drafts it took, and the report&rsquo;s{' '}
            <C>second_reading</C> block totals these. It is on by default for authoring projects; pass{' '}
            <C>{`"second_reading": false`}</C> to <C>POST /projects</C> to turn it off. Judgment
            templates don&rsquo;t use it: several clinicians review each item instead.
          </p>
          <p>
            <b>Agent traces.</b> For <C>agent_trace_eval</C>, send <C>trace</C> as a list of steps
            (objects or strings) or as text. Clinicians see it as numbered steps; your results keep
            it exactly as you sent it. The report adds <C>clinical.steps</C>: where trajectories
            first break (<C>median_first_failed_step</C> and the distribution).
          </p>
          <p>
            <b>Tune a template to your own rubric.</b> <C>GET /templates</C> also returns each template&rsquo;s
            full <C>eval_config</C>. Take the closest one, edit it to fit your exact task (add rating axes,
            change fields or context), and submit it as a custom <C>eval_config</C> below instead of{' '}
            <C>template</C> — so you start from a working, validated config, not a blank page.
          </p>

          <h3>Custom config (advanced)</h3>
          <p>Define your own task from scratch:</p>
          <Code>{`curl -X POST "$BASE/projects" \\
  -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "Clinical response evaluation",
    "eval_config": {
      "title": "Clinical response review",
      "purpose": "evaluate",
      "schema": {
        "input": "text",
        "context": [
          { "key": "scenario",   "label": "Patient message" },
          { "key": "prediction", "label": "Model response" }
        ],
        "classes": ["Routine", "Urgent", "Emergency"],
        "case_id_field": "case_id",
        "fields": {
          "verdict":       { "type": "single", "options": ["Correct", "Incorrect", "Partial"], "required": true },
          "correct_label": { "type": "from_classes", "visible_when": "verdict!=Correct" },
          "critical_miss": { "type": "structured" },
          "notes":         { "type": "text" }
        }
      }
    },
    "webhook_url": "https://your-app.com/hooks/senebiclabs"
  }'`}</Code>
          <h3>Response</h3>
          <Code>{`{
  "ok": true,
  "project_id": "fc64fb22-...",
  "webhook_secret": "a28e0736cb92..."
}`}</Code>
          <p>
            <b>Save the <C>webhook_secret</C>.</b> It is returned once, only when you register
            a <C>webhook_url</C>, and is used to verify webhook authenticity (see{' '}
            <a href="/docs/webhooks" style={{ color: '#fff' }}>Webhooks</a>). Treat it like a password.
          </p>
          <p>
            This example is an <b>evaluation</b> project: each item carries a <C>prediction</C>,
            clinicians return a <C>verdict</C> of <C>Correct</C>, <C>Incorrect</C>, or <C>Partial</C>,
            and the report scores accuracy. For a <b>creation</b> project, omit <C>prediction</C> and set
            <C>fields</C> to the labels you want produced; the results come back as content-and-label
            pairs with no scorecard.
          </p>
        </section>
    </>
  )
}
