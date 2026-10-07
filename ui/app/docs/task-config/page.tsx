import type { Metadata } from 'next'
import { C } from '../_ui'

export const metadata: Metadata = {
  title: 'Task config',
  description: 'The eval_config that decides what clinicians see and fill in.',
  alternates: { canonical: 'https://senebiclabs.com/docs/task-config' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-eyebrow">Reference</span>
          <h1>Task config</h1>
          <p>
            The <C>eval_config</C> defines what clinicians see and fill in. Key fields:
          </p>
          <ul>
            <li><C>purpose</C>: <C>evaluate</C> (grade a model output), <C>label</C> (categorise / annotate data), or <C>create</C> (produce gold answers, preferences, or ratings). Defaults to <C>evaluate</C>; it sets the reviewer workflow and the deliverable.</li>
            <li><C>instructions</C>: your rubric, shown to clinicians at the top of every task &mdash; what to evaluate, the standard, what counts as an error, edge cases. Optional, but it&rsquo;s the single biggest lever on answer quality and reviewer agreement. Use line breaks to separate points. (Templates ship with a starter rubric you can tune.)</li>
            <li><C>adjudicate</C>: <C>true</C> holds any item where reviewers disagree for a senior reviewer to resolve, instead of shipping the majority vote. Recommended for judgment work; the judgment templates set it for you. Optional (default <C>false</C>).</li>
            <li><C>auto_deliver</C>: by default a finished batch is held for a human sign-off before it&rsquo;s released to you (status stays <C>in_review</C> until then). Set <C>true</C> for hands-off delivery the moment every item is done. Optional (default <C>false</C>).</li>
            <li><C>input</C>: <C>text</C> (shows the <C>context</C> fields), <C>image</C> (each item needs an <C>image</C> URL), or <C>audio</C> / <C>video</C> (each item needs an <C>audio</C> / <C>video</C> URL; a clinician plays it, streamed straight from your storage).</li>
            <li><C>context</C>: for text tasks, which data keys to show the clinician, in order.</li>
            <li><C>classes</C>: the label set used by <C>from_classes</C> and <C>structured</C> fields.</li>
            <li><C>case_id_field</C>: which item field ties a result back to your own record.</li>
            <li>
              <C>field_order</C>: the order clinicians are asked the fields, as a list of field
              names. Worth setting — configs are stored as JSON, whose key order is not preserved,
              so without it the form is ordered arbitrarily and a clinician can be asked for a
              rationale before the verdict it explains. Templates set it for you; names you leave
              out are asked last.
            </li>
            <li>
              Each field takes a <C>label</C> (the question a clinician reads — without one the
              field name is prettified, so <C>correct_label</C> reads as &ldquo;Correct label&rdquo;)
              and an optional <C>hint</C>.
            </li>
            <li><C>primary_field</C>: which answer decides reviewer agreement, and so which items are held for adjudication. Defaults to the first required <C>single</C> / <C>from_classes</C> field.</li>
          </ul>
          <p><C>fields</C> is a map of what the clinician fills. Each has a <C>type</C>:</p>
          <ul>
            <li><C>single</C>: choose one of <C>options</C>.</li>
            <li><C>from_classes</C>: choose one of the project <C>classes</C>.</li>
            <li><C>structured</C>: yes or no, plus which finding (from classes).</li>
            <li><C>scale</C>: a rating from 1 to <C>max</C>.</li>
            <li><C>flag</C>: a single checkbox.</li>
            <li><C>text</C>: free-text notes (<C>rows</C> sets the box height for long-form).</li>
            <li><C>spans</C>: highlight text in the model output and tag each span with one of <C>options</C> (text input only).</li>
          </ul>
          <p>
            Any field can add <C>required: true</C>, <C>{'visible_when: "field!=value"'}</C>, and <C>{'hint: "..."'}</C> (a one-line note shown under the field&rsquo;s label to guide the clinician).
          </p>
        </section>
    </>
  )
}
