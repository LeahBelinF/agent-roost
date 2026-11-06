// roost-links: the PRs and tickets of the Session in the focused tab, as chips that open in the browser.
// Installed by `roost setup herdr`; edit the copy in agent-roost's roost/herdr/ folder, not this one.
// It asks `roost links --terminal <address>` (cached by roost for two minutes, so gh isn't run every time).
import { View, div } from "gpui-kit";
import { h_flex } from "gpui-base";
import { exec, focused, theme } from "herdr";

const CHECK_MS = 1000;   // how often to look for a newly focused terminal (no redraw unless it changed)
const REFRESH_MS = 60000; // how often to ask again for the focused one

export default class RoostLinks extends View {
  init(_props, cx) {
    this.links = {};      // terminal address -> roost's answer
    this.asking = null;   // the address being asked about
    this.shown = null;    // the address last asked about
    cx.timer.every(CHECK_MS, (cx) => this.check(cx, false));
    cx.timer.every(REFRESH_MS, (cx) => this.check(cx, true));
    this.check(cx, false);
  }

  check(cx, again) {
    const term = focused()?.terminal;
    if (!term || this.asking === term || (!again && term === this.shown && this.links[term])) return;
    this.asking = term;
    this.shown = term;
    cx.spawn(async (cx) => {
      try {
        const r = await exec("roost", ["links", "--terminal", term]);
        this.links[term] = JSON.parse(r.stdout || "{}");
      } catch (e) {
        this.links[term] = { error: String(e) };
      }
      this.asking = null;
      cx.notify();
    });
  }

  chip(id, label, color, url, title) {
    const t = theme();
    return div().id(id).px(7).py(1).rounded(4).bg(t.raised).text_color(color).hover((s) => s.bg(t.hover))
      .on_click(() => exec("open", [url])).child(div().max_w(260).truncate().child(title ? `${label}  ${title}` : label));
  }

  render() {
    const t = theme();
    const term = focused()?.terminal;
    const l = term ? this.links[term] : null;
    const bar = h_flex().id("root").size_full().min_w(0).px(10).gap(6).items_center().bg(t.chrome)
      .border_b(1).border_color(t.border).text_size(11.8).text_color(t.dim);
    if (!l) return bar.child(term && this.asking === term ? "roost: looking…" : "");
    if (l.error) return bar.child(`roost links: ${l.error}`);
    if (!l.session) return bar.child("No Claude Session in this pane");
    const color = { open: t.ansi2, merged: t.ansi5, closed: t.dim, draft: t.muted };
    const prs = (l.prs ?? []).map((p, i) =>
      this.chip(`pr-${i}`, `#${p.number} ${p.state || "?"}`, color[p.state] ?? t.muted, p.url, p.title));
    const tickets = (l.tickets ?? []).map((k, i) => this.chip(`tk-${i}`, `CU-${k.id}`, t.accent, k.url));
    if (!prs.length && !tickets.length) return bar.child("No PRs or tickets yet");
    return bar.child(h_flex().id("chips").flex_1().min_w(0).gap(6).items_center().overflow_x_scrollbar()
      .children([...prs, ...tickets]));
  }
}
