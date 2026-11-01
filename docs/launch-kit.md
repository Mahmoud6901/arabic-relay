# Arabic Relay launch kit

Prepared 1 November 2026. These are drafts for the maintainer to review and publish. No posts or invitations have
been sent. Update the release/signing status before using these after a future release.

## Links

- Project: https://github.com/Mahmoud6901/arabic-relay
- Windows download: https://github.com/Mahmoud6901/arabic-relay/releases/latest
- Arabic guide: https://github.com/Mahmoud6901/arabic-relay/blob/main/README.ar.md
- Feedback: https://github.com/Mahmoud6901/arabic-relay/issues/new?template=first_use_feedback.yml
- Current review screenshot: [quick-capture-review.png](../assets/imgs/quick-capture-review.png)

The screenshot uses typed sample text in the actual review interface. It is not a recording or proof of speech
recognition accuracy. Do not present it as a measured end-to-end speech demo.

## Arabic LinkedIn / community post

لو بتشرح شغلك بالعربي، لكن عايز تبعت تعليمات إنجليزية واضحة لـ Codex أو Claude Code، عملت أداة اسمها Arabic Relay.

بتتكلم بالعربي والإنجليزي مع بعض، تراجع البرومبت وتعدّله، وتختار جلسة البرمجة اللي تبعته لها.

فيه وضعين:
• Craft a prompt: يحوّل ملاحظاتك لتعليمات منظمة.
• Inline translation: يترجم العبارات العربية ويحافظ على النص الإنجليزي الموجود خارجها.

الإرسال بيحصل لما تضغط Send. التطبيق بيدعم T3 Code وجلسات Codex وClaude Code المحلية، مش محادثات ChatGPT وClaude العادية.

مفتوح المصدر ومجاني على Windows. Whisper يشتغل محليًا للصوت، وتجهيز البرومبت بيستخدم حساب Codex أو Claude Code الخاص بيك أونلاين وحدود استخدامه.

لسه مشروع جديد، وبدور على ناس تجرّبه في مهمة واحدة وتقولّي: إيه اللي سهّل الشغل، وإيه اللي عطّلها؟
نسخة Windows الحالية لسه من غير توقيع ناشر Authenticode؛ طلب SignPath قيد المراجعة.

التحميل والتفاصيل: https://github.com/Mahmoud6901/arabic-relay

## English LinkedIn / community post

I built Arabic Relay for developers who explain tasks in Arabic while working with English coding tools.

Speak or type Arabic mixed with English terms, review the English prompt, then send it to selected T3 Code,
Codex, or Claude Code sessions. Craft a prompt organizes your notes into instructions; Inline translation
translates Arabic phrases while preserving the existing English outside those phrases.

It is a free, MIT-licensed Windows app. Local Whisper keeps transcription audio on your computer. Prompt
preparation uses your signed-in Codex or Claude Code provider online and its usage limits. Sending requires
an explicit click. Regular ChatGPT and Claude conversations are not supported destinations.

I’m looking for early testers: try one task and tell me where setup, recognition, or session selection gets in
your way. The current Windows release has an update-verification signature but no Authenticode publisher
signature yet; our SignPath application is pending.

Download, source, and feedback: https://github.com/Mahmoud6901/arabic-relay

## Short post for X

Built Arabic Relay: speak Arabic + English, review an English prompt, send it to T3 Code, Codex or Claude Code sessions.

Free, open-source Windows app. Looking for early testers.

https://github.com/Mahmoud6901/arabic-relay

## Personal invitation — send only to someone who welcomes it

عملت أداة Windows اسمها Arabic Relay بتحوّل كلامك العربي والإنجليزي لبرومبت تراجعه وتبعته لجلسة برمجة.
لو ده قريب من شغلك، تحب تجربها في مهمة صغيرة وتقولّي إيه اللي عطّلك؟ محتاجة Codex CLI أو Claude Code مسجل دخول
لتجهيز البرومبت. نسخة Windows الحالية لسه من غير توقيع ناشر، والتفاصيل موضحة في صفحة المشروع.
https://github.com/Mahmoud6901/arabic-relay

## 40-second demo recording plan

Use a disposable demo project/session with no private data. Start with Relay and the chosen coding tool already
set up. Use the shortcut actually selected in Settings. Do not show account identifiers, tokens, or private session
titles. This plan is not a completed video.

| Time | Screen action | Arabic narration | English subtitle |
|---|---|---|---|
| 0–5s | Show Relay and its selected shortcut. | بتفكر بالعربي وبتكتب تعليمات البرمجة بالإنجليزي؟ | Think in Arabic, work with English coding tools? |
| 5–13s | Open Quick capture. Record the sample below with the selected provider. | راجع صفحة الـ login وحسّن الـ error messages من غير ما تغيّر الـ authentication flow. | Review the login page and improve error messages without changing authentication. |
| 13–22s | Stop. Show the real processing state, then the actual result. | Relay بيجهّز البرومبت، وأنا براجعه قبل الإرسال. | Prepare the prompt, then review it. |
| 22–30s | Edit a word if needed. Choose only the disposable session. | أقدر أعدّل الكلام وأختار الجلسة اللي محتاجها. | Edit the text and choose the destination. |
| 30–36s | Send only after checking the demo recipient; show the real acknowledgement. | الإرسال بيحصل لما أضغط Send. | Nothing is sent until I click Send. |
| 36–40s | Show the project URL and Windows download link. | جرّبه وقولّي إيه اللي محتاج يتحسّن. | Try it and tell me what needs work. |

If processing takes longer, extend the clip or mark a cut as “processing wait shortened.” Never imply that an
edited clip is a speed benchmark. With Wispr Flow, first open Relay's popup, then use Flow's own shortcut.
If no disposable destination is available, stop at review/copy and label the demo accordingly; do not fake a sent state.

## First ten testers

Invite developers who already use Arabic and one of the supported coding tools. Follow each community's promotion
rules. Ask for one task and feedback, not a star as a condition of access. There is no automatic outreach or tracking.

Keep any tester roster private. Track only voluntary, minimal feedback: installation completed, prompt prepared,
delivery/copy completed, biggest obstacle, and whether they used it again. Do not commit names or contact details.
Aim first for 10 successful trials and 5 repeat users; these are goals, not reported usage numbers.

Use the bilingual first-use issue template for public feedback. Ask people to use synthetic examples instead of
private recordings or prompts. Address repeated blockers before broadening promotion.

## Show HN draft — after first-use feedback

Title: Show HN: Arabic Relay — Arabic/English dictation to coding-session prompts

Link: https://github.com/Mahmoud6901/arabic-relay

Opening comment:

I built Arabic Relay to reduce the repeated rewriting between spoken Arabic, English technical terms, and
instructions for coding agents. It is a Windows desktop app with local Whisper transcription, optional online
speech providers, and prompt preparation through the user's own Codex CLI or Claude Code login.

Users can review/edit the English result and explicitly send it to selected local coding sessions or T3 Code.
There is also an inline mode that translates Arabic spans while preserving the English outside them.

It is an early MIT-licensed project. I would especially appreciate feedback on mixed-language recognition,
first-time setup, and what would make this useful in a real development workflow. The README describes the
provider requirements, online processing, current Windows signing status, and supported destinations.

Publish only when people can try the release. Stay available to answer questions. Do not coordinate votes or ask
friends to upvote. See the [Show HN guidelines](https://news.ycombinator.com/showhn.html).
