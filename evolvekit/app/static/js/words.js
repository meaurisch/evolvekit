/* evolvekit app: what a study's state says, in the design's words.
   One place decides the verdict mark, Pepper's phase and the card's lines, so
   Home, the Study page and the companion never disagree. The rule itself is
   the server's: the page says "use it" only when the final check's decision
   is "use" (`decision` from the run). */
(function () {
  "use strict";
  var EK = window.EK;

  /** The verdict mark for a decision: use, keep, worse, none (one case, nothing to check), open (no answer yet), error. */
  EK.markOf = function (s) {
    if (!s) return "open";
    if (s.kind === "failed" || s.kind === "broken") return "error";
    if (s.kind !== "finished") return "open";
    return { use: "use", keep: "keep", worse: "worse", single: "none", none: "none", skipped: "none" }[s.decision] || (s.tone === "bad" ? "worse" : s.tone === "warn" ? "keep" : "none");
  };

  /** Pepper's phase. Every finished study hatches: bright on use it, dull on every other answer.
      A final check that was stopped stays in the cocoon, asleep. */
  EK.pepperPhase = function (s) {
    if (!s) return "egg";
    if (s.kind === "draft" || s.kind === "queued" || s.kind === "broken") return "egg";
    if (s.kind === "finished") return s.decision === "use" ? "butterfly" : "butterfly-dull";
    if (s.check_stopped) return "cocoon-asleep";
    if (s.phase === "check") return "cocoon-late";
    if (s.phase === "starting") return "egg";
    var r = s.rounds || {};
    if (s.kind === "running") {
      if (s.continued || !r.planned) return "caterpillar";
      return (r.done || 0) >= r.planned / 2 ? "cocoon" : "caterpillar";
    }
    return "caterpillar"; // stopped or failed in the search
  };

  /** "Keep today's settings", "Keep today's data" or "Keep today's code", by what the study changes. */
  EK.keepWords = function (s) {
    return s && s.what === "data" ? "Keep today’s data" : s && s.what === "code" ? "Keep today’s code" : "Keep today’s settings";
  };

  /** "on cost as PyVRP counts it (lower)": the goal's measure and which way is better. */
  EK.goalWords = function (goal) {
    if (!goal || !(goal.says || goal.label)) return "";
    return " on " + (goal.says || goal.label) + " (" + (goal.direction === "higher" ? "higher" : "lower") + ")";
  };
  /** The verdict in a word or two, for a kicker: the headline itself is the number. */
  EK.verdictWord = function (d) {
    return { use: "Use it", keep: "Keep", worse: "Worse", single: "No answer", skipped: "Nothing better" }[d] || "Finished";
  };
  /** The answer's headline and its second line, from the decision and the numbers. */
  EK.answerWords = function (s) {
    var d = s.decision, mean = s.mean, on = EK.goalWords(s.goal);
    if (d === "use") return { head: EK.better(mean) + on, sub: "" };
    if (d === "keep") return { head: EK.keepWords(s), sub: (mean != null ? EK.better(mean) + on + ", but not sure enough" : "Not proven on new cases") };
    if (d === "worse") return { head: EK.better(mean) + on, sub: EK.keepWords(s) + "." };
    if (d === "single") return { head: (mean != null ? EK.better(mean) : "A number") + on + " on one case", sub: "One case gives no range, so no answer." };
    if (d === "skipped" && s.not_run) return { head: "The search did not run", sub: (s.verdict && s.verdict.line) || "Continue it to give the search its time." };
    if (d === "skipped") return { head: "Nothing beat the starting point", sub: EK.keepWords(s) + "." };
    return { head: "No answer", sub: s.line || "" };
  };

  /** The caption under a range: the chance and the range's ends, or why there is no range. A comparison
      written before A1 (`posterior: false`) keeps its own words for the chance. A keep that failed more
      runs than the starting point (`failed_more`, decision 14a) says its chance does not decide. */
  EK.rangeCaption = function (s) {
    var ci = s.ci95;
    if (!ci) return "One case: a number, no range";
    var range = EK.rangeWords(ci[0], ci[1]), legacy = s.posterior === false, rule = legacy ? 0.9 : 0.95;
    if (s.decision === "keep" && s.failed_more) return "The chance does not decide: it failed " + s.failed_more + " run" + (s.failed_more === 1 ? "" : "s") + " the starting point finished · " + range;
    if (s.decision === "keep" && s.chance != null && s.chance < rule) return (legacy ? "Chance under 9 in 10 · " : "Chance under 19 in 20 · ") + range;
    return s.chance != null ? EK.capital(EK.chanceWords(s.chance, s.posterior)) + " · " + range : range;
  };

  /** What a start of a scheduled study says (POST /start's `scheduled` answer): "Scheduled: next window
      Sat 00:00", "Scheduled: its window is open now, until Sun 23:59", or after a finished campaign
      "Scheduled a new campaign: the last one finished · next window Sat 00:00". */
  EK.scheduledWords = function (res) {
    var words = (res && res.words) || "";
    return res && res.renewed ? "Scheduled a new campaign: the last one " + res.renewed + " · " + words : "Scheduled: " + words;
  };

  /** The rehearsal notice (`evolvekit app --clock`), on Home and on the Study page (persona p4, H3;
      round 2, I5): "Rehearsal clock: Mon 16 Nov 08:39, 120 × real time. Times of the windows follow
      this clock; other dates are the real time." `clock` is the API's `{line}`. */
  EK.rehearsalWords = function (clock) {
    return EK.capital(clock.line) + ". Times of the windows follow this clock; other dates are the real time.";
  };

  /** The name of the Set up row a study's first problem belongs to (`missing`: its step and key):
      "What to tune", "What may change: code", "Data changes", "Goal", "Time", "When it runs"; "Cases"
      before Set up; "the plan" for anything else. */
  EK.missingWords = function (m) {
    var key = String((m && m.key) || ""), step = (m && m.step) || 7;
    if (/^schedule\b/.test(key) || /\bschedule\b/.test(String((m && m.text) || ""))) return "When it runs";
    if (step < 4) return "Cases";
    if (step === 4) return key.indexOf("vary.code") === 0 ? "What may change: code" : key.indexOf("vary.data") === 0 ? "Data changes" : "What to tune";
    if (step === 5) return "Goal";
    if (step === 6) return "Time";
    return "the plan";
  };

  /** A Home card from one study of GET /api/home. */
  EK.cardOf = function (s) {
    var phase = EK.pepperPhase(s), r = s.rounds || {};
    var c = { slug: s.slug, name: s.name, mark: EK.markOf(s), pepper: phase, archived: !!s.archived };
    var round = r.planned ? "round " + Math.min((r.done || 0) + 1, r.planned) + " of " + r.planned : (r.done ? "round " + (r.done + 1) : "");
    if (s.kind === "draft") {
      c.kicker = "Draft";
      // What is missing, by its Set up row's name (persona p4, H6): "Set up not finished: Goal";
      // a draft with nothing missing is ready, only not started.
      c.head = s.step >= 4 ? (s.missing ? "Set up not finished: " + EK.missingWords(s.missing) : "Ready to start: not started yet") : "Cases still to add";
      c.sub = s.harness || "";
      c.pepper = "egg";
      c.href = (s.step >= 4 ? "#/setup/" : "#/start/") + EK.q(s.slug);
    } else if (s.kind === "queued") {
      c.kicker = "Queued";
      c.head = s.waiting_for ? "Starts after " + s.waiting_for : "Waits for its turn";
      c.sub = "Starts by itself" + (s.hours ? " · about " + EK.hours(s.hours) : "");
      c.timeline = { rounds: r.planned || 1, done: 0, queued: true };
    } else if (s.kind === "running") {
      c.kickerTone = "accent";
      if (s.phase === "check") {
        c.kicker = "Final check" + (s.check && s.check.planned ? " · " + s.check.done + " of " + s.check.planned + " runs" : "");
        c.head = "No answer yet";
        c.sub = "Checking on held-back cases" + (s.finish_at ? " · done around " + s.finish_at : "");
      } else if (s.continued) {
        c.kicker = "Searching again" + (round ? " · " + round : "");
        // More time asked for, or a restart after a stop or a crash: never one said for the other (persona p5, J6).
        var more = s.added_h ? "Continued: " + EK.hours(s.added_h) + " more" : null;
        c.head = more || (s.restarted ? "Continued after a restart" : "Continued");
        c.sub = (more ? "The search goes on from where it was" : "Nothing lost") + (s.finish_at ? " · done around " + s.finish_at : "");
      } else {
        c.kicker = s.phase === "starting" ? "Starting" : "Running" + (round ? " · " + round : "");
        // The best so far in `study status`'s words (the server's `best_says`), on the cases it learned from.
        c.head = s.best_says ? "Best so far: " + s.best_says : "Nothing better yet";
        c.sub = (s.best_says ? "On the cases it learned from, not checked yet" : "Not checked yet") + (s.finish_at ? " · done around " + s.finish_at : "");
      }
      c.timeline = { rounds: r.planned || Math.max(1, (r.done || 0) + 1), done: r.done || 0, running: s.phase === "check" ? null : (r.done || 0) + 1,
        check: s.phase === "check" && s.check ? [s.check.done, s.check.planned] : null };
    } else if (s.kind === "finished") {
      // One world (design 3.4): the verdict's own line and advice, the label in the state line's words.
      var v = s.verdict || { says: String(s.line || "finished").replace(/^finished · /, "").split(":")[0], line: "", advice: "" };
      c.kicker = EK.capital(v.says);
      c.head = v.line || EK.capital(v.says);
      // Why a keep is still keep when every held-back case improved (persona p4, H2): under the line.
      if (v.why_keep) c.why = v.why_keep;
      c.sub = v.advice || "";
      if (s.ci95 && s.mean != null) c.range = [s.mean, s.ci95[0], s.ci95[1]];
      // Stopped before its first round (persona p5, J1): the way on is Continue, from the card itself, as the
      // Study page's (fix round 1, M4); a study with a schedule keeps the time for its next window.
      if (s.not_run) {
        c.kickerTone = "warning";
        if (s.can_continue) {
          var more = EK.hours(s.continue_h || 1);
          c.actions = [{ text: s.scheduled ? "Search " + more + " more in the next window" : "Continue for " + more + " more",
            act: "continue-run", arg: s.slug, kind: "primary" }];
        }
      }
    } else if (s.kind === "campaign") {
      // Between windows (D20): the campaign's own words; no claim, the run is not checked yet.
      var camp = s.campaign || {};
      c.kicker = camp.line || "Campaign";
      if (camp.tone === "warn") c.kickerTone = "warning";
      c.head = s.first ? "Waits for its first window" : "Between windows";
      c.sub = r.planned ? (r.done || 0) + " of " + r.planned + " rounds done · not checked yet" : "";
      if (s.first) {
        c.pepper = "egg";
        c.href = "#/setup/" + EK.q(s.slug);
      } else if (r.planned) {
        c.timeline = { rounds: r.planned, done: r.done || 0, stoppedAt: (r.done || 0) + 1 };
      }
    } else if (s.kind === "stopped") {
      c.kicker = s.check_stopped ? "Final check stopped" : "Stopped" + (r.done ? " · round " + (r.done + 1) : "");
      c.head = s.check_stopped ? "No answer yet" : s.best_says ? "Best so far: " + s.best_says : "Stopped by you";
      c.sub = s.check_stopped ? "The runs so far are kept." : s.best_says ? "On the cases it learned from, not checked yet" : "Not checked yet";
      c.timeline = { rounds: r.planned || Math.max(1, r.done || 1), done: r.done || 0, stoppedAt: (r.done || 0) + 1 };
    } else if (s.kind === "failed") {
      c.kicker = "Stopped by an error";
      c.kickerTone = "critical";
      c.head = r.done != null ? "The study stopped in round " + ((r.done || 0) + 1) : "The study stopped";
      c.sub = s.error ? String(s.error).split("\n")[0].slice(0, 90) : "Tries up to the stop are kept.";
      c.timeline = { rounds: r.planned || Math.max(1, (r.done || 0) + 1), done: r.done || 0, running: (r.done || 0) + 1, failed: true };
    } else {
      c.kicker = "Cannot be read";
      c.kickerTone = "critical";
      c.head = "This study cannot be opened";
      c.sub = s.line || "";
      c.pepper = null;
    }
    // A study with a schedule: its campaign's line ends the sub-line ("… · Campaign · next window Sat 00:00"),
    // beside the card's own words -- a finished run's verdict stays the head (D49).
    if (s.campaign && s.campaign.line && s.kind !== "campaign") c.sub = (c.sub ? c.sub + " · " : "") + s.campaign.line;
    // A later run must not hide an earlier use: "An earlier run (<id>) says use: <its verdict line>".
    if (s.earlier && s.earlier.says) c.note = s.earlier.says;
    if (s.archived) c.kicker = "Archived · " + c.kicker.charAt(0).toLowerCase() + c.kicker.slice(1);
    if (!c.href) c.href = s.kind === "broken" ? "#/" : EK.studyHref(s.slug, null);
    return c;
  };
})();
