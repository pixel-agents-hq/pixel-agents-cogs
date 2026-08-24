"""Server-rendered landing menu listing every guild's own office "universe".

Deliberately not part of the vendored Pixel Agents SPA: issue #4 asked for a
menu of servers on the dashboard, not a client-side switcher inside the
webview bundle, so this is a small, independent HTML page.
"""

from __future__ import annotations

import html

_STYLE = """<style>
  body { font-family: sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; }
  h1, h2 { font-weight: 600; }
  h1 { font-size: 1.5rem; }
  h2 { font-size: 1.1rem; margin-top: 2rem; }
  ul { list-style: none; padding: 0; }
  li { margin: 0.5rem 0; }
  .office-link {
    display: block; padding: 0.75rem 1rem; border-radius: 0.5rem;
    background: #f0f0f0; color: #111; text-decoration: none;
  }
  .office-link:hover { background: #e0e0e0; }
</style>"""

# Background-fetches a ticket the same way TICKET_SHIM does (login-gated
# `session`, silently swallowed for an anonymous visitor), then asks the
# `servers` endpoint which private guilds that ticket's holder can see, and
# reveals them -- never a page-load-time redirect, so a logged-out visitor's
# first paint is exactly the public list rendered below.
_SCRIPT = """<script>
(function () {
  function link(guildId, name) {
    var a = document.createElement('a');
    a.className = 'office-link';
    a.href = 'office-login?guild=' + encodeURIComponent(guildId);
    a.textContent = name;
    return a;
  }
  fetch('session', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) { return (data && data.ticket) || null; })
    .then(function (ticket) {
      if (!ticket) { return null; }
      return fetch('servers?ticket=' + encodeURIComponent(ticket), {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      }).then(function (r) { return r.ok ? r.json() : null; });
    })
    .then(function (data) {
      var list = document.getElementById('private-guilds');
      var section = document.getElementById('private-guilds-section');
      if (!list || !section || !data || !data.private || !data.private.length) { return; }
      data.private.forEach(function (guild) {
        var li = document.createElement('li');
        li.appendChild(link(guild.id, guild.name));
        list.appendChild(li);
      });
      section.hidden = false;
    })
    .catch(function () {});
})();
</script>"""


def render_menu(public_guilds: list[tuple[int, str]]) -> str:
    """Render the landing page: direct links to every public guild's own
    office, plus a hidden section the inline script reveals with whichever
    private guilds the (optionally logged-in) viewer belongs to."""

    if public_guilds:
        items = "".join(
            f'<li><a class="office-link" href="office?guild={guild_id}">'
            f"{html.escape(name)}</a></li>"
            for guild_id, name in public_guilds
        )
        public_section = f"<ul>{items}</ul>"
    else:
        public_section = "<p>No public offices yet.</p>"

    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>Pixel Agents</title>"
        + _STYLE
        + "</head><body>"
        + "<h1>Pixel Agents</h1>"
        + public_section
        + '<section id="private-guilds-section" hidden>'
        '<h2>Your servers</h2><ul id="private-guilds"></ul></section>' + _SCRIPT + "</body></html>"
    )


__all__ = ["render_menu"]
