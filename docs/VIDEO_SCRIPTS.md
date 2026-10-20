# DFIR explainer video: two scripts (about 2.5 minutes each)

Speak at a relaxed pace (about 130 words a minute). **[Screen]** lines say what to show while you speak.
Before recording: start the app, have one case ready (a fake face image with a few copies found), and use the light Neomorphism theme.

---

## Script A: "What if you woke up to this?" (story first)

**[0:00 – 0:25] Hook. Camera on you, no screen yet.**

Imagine you wake up tomorrow, and a video of *you* is everywhere. Your face. Your voice. Saying something you never said.
Your phone is blowing up. Friends are sharing it. And you don't know what to do first.

**[0:25 – 0:50] The problem. Quick cuts of a search page, a "report" form, a lawyer's website.**

You open the internet and it's a mess. One site says "upload it and get a score." Another says "report it here."
Nobody tells you the order. Is it fake? Can you prove it? Where did it spread? Who do you report it to, and by when?
Most tools give you one number, and then you're on your own.

**[0:50 – 1:10] The app. Screen: Home dashboard.**

So I built DFIR, a Deepfake Investigation and Response system. You don't need to know anything technical.
You paste the link to the post, or upload the file. That's it.

**[1:10 – 1:40] Step 1: Is it fake? Screen: paste a link, press Start Investigation, then the verdict card.**

DFIR opens the page, saves a copy as evidence, and checks it. You get one plain answer, like "Likely fake, 85 percent,"
with the reasons underneath. And it's honest: if the tools can't tell, it says "Unknown." It never pretends.

**[1:40 – 2:00] Step 2: Where did it spread? Screen: Where it appeared, the map of copies.**

Next it searches for other copies, other accounts, other versions, and shows how they connect.
Seen with your own eyes, not guessed.

**[2:00 – 2:25] Step 3: What do I do now? Screen: Take action, with the checklist and deadlines.**

And this is the part I care about most. It tells you exactly what to do next, in order: preserve the evidence,
report to the platform, file the complaint, with the wording prepared for you. It tracks the deadlines and what you've filed,
so you never wonder "what now?"

**[2:25 – 2:45] Close. Camera on you.**

DFIR never accuses anyone and never files anything without you. It gives you evidence, and a next step.
So when this happens, you're not lost. You're ready.

---

## Script B: "Detection is not enough" (product comparison)

**[0:00 – 0:20] Hook. Camera on you.**

This is a deepfake detection tool. And so is this one. And this one. They all do the same thing: you upload an image, you get a score.
And then what?

**[0:20 – 0:50] The gap. Screen: three generic tools showing just a percentage.**

Say it says "92 percent fake." What do you do with that number? You can't show it to the police.
You can't send it to a platform. You don't know where else it was posted, and you don't know what to do next.
Detection is one step. Victims need the whole path, and most tools stop at the first step.

**[0:50 – 1:10] Our answer. Screen: Home dashboard.**

DFIR covers that whole path, in one place: Detect, Explain, Trace, Preserve, Report, and Monitor.

**[1:10 – 1:35] Detect and explain. Screen: verdict card, then Forensic modules.**

First, it detects with a commercial detector, Hive, backed by local models as a second opinion.
Then it explains: six forensic modules look at the camera, the editing, the video, the audio and the file's origin.
Every finding says how sure it is, and what it cannot tell you.

**[1:35 – 1:55] Trace and preserve. Screen: spread map, then Evidence package.**

It finds other copies and shows how they connect. Everything is saved read-only, with hashes and a tamper-evident log,
so it's real evidence, not a screenshot.

**[1:55 – 2:20] Act. Screen: Take action.**

Then comes the step nobody else gives you: what to do next. A takedown and complaint tracker with the right order, the deadlines,
and prepared text. Monitoring tells you if a copy is removed or comes back.

**[2:20 – 2:45] Close. Camera on you.**

Other tools tell you *if* it's fake. DFIR tells you what you can prove, and what to do next, so the user never gets stuck.
It stays careful: unknowns stay unknown, leads stay leads, and you stay in control.

---

## Notes so the video stays accurate

* Say "most tools stop at a score," not "no other tool exists." That is true of the tools I know of, but I haven't checked every one.
* Don't promise legal deadlines. The deadlines in the app are marked "verify" and depend on the platform and country.
* Say "evidence preserved and hashed," not "court-proof." Whether evidence is accepted is for the court and a lawyer.
* The forensic modules are validated on simulated data, not real cameras. If asked, say that plainly.
* The app only checks real photos and videos with the tools it has. Audio deepfakes are not covered by a trained detector yet.
