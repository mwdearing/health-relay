# pyright: reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import html
from io import BytesIO
from typing import Final

import segno

from health_bridge.receiver.pairing import (
    ReceiverPairingBundle,
    ReceiverPairingInvitationBundle,
)

SETUP_PAGE_TITLE: Final = "HealthRelay Pairing"
SETUP_PAGE_DELETE_NOTICE: Final = "Delete this setup page after pairing."

# Shared stylesheet for both setup pages. Light and dark follow the viewer's
# system appearance; every text pair clears WCAG AA (4.5:1). The QR code keeps a
# white plate in both modes so iPhone Camera can read it.
_SETUP_PAGE_STYLE: Final = """
    :root {
      color-scheme: light dark;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --bg: #edf2f2; --card: #ffffff; --inset: #f2f6f6; --ink: #0f1b1f;
      --muted: #4b5b61; --line: #c9d6d8; --field: #6f8287;
      --accent: #0b3d4a; --on-accent: #ffffff;
      --secondary: #e3f1f0; --on-secondary: #0b3d4a;
      --warn-bg: #fff4e0; --warn-ink: #7a4100;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #07222e; --card: #0e2f3b; --inset: #0a2833; --ink: #eaf6f5;
        --muted: #a9c1c4; --line: #1e4553; --field: #6d949c;
        --accent: #5eead4; --on-accent: #07222e;
        --secondary: #15404d; --on-secondary: #5eead4;
        --warn-bg: #3a2a0a; --warn-ink: #fcd9a0;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      grid-template-columns: minmax(0, 1fr);
      padding: max(1rem, env(safe-area-inset-top)) 1rem
        max(1rem, env(safe-area-inset-bottom));
      background: var(--bg); color: var(--ink); line-height: 1.5;
    }
    main {
      width: min(100% - 2rem, 720px); margin: 1rem auto;
      padding: clamp(1.25rem, 4vw, 2rem);
      border: 1px solid var(--line); border-radius: 28px;
      background: var(--card); overflow: visible;
    }
    .brand {
      display: flex; align-items: center; gap: 0.75rem;
      margin-bottom: 1.25rem; font-size: 1.25rem; font-weight: 700;
    }
    .brand svg {
      width: 40px; height: 40px; flex-shrink: 0;
      border-radius: 9px; box-shadow: 0 0 0 1px var(--line);
    }
    h1 { margin: 0 0 0.5rem; font-size: clamp(1.8rem, 5vw, 2.6rem); line-height: 1.15; }
    h2 { margin: 1.5rem 0 0.25rem; font-size: 1.25rem; }
    .eyebrow { margin: 0 0 0.25rem; color: var(--muted); font-weight: 600; }
    .hint { color: var(--muted); }
    .qr-shell {
      display: flex; justify-content: center; align-items: center;
      width: 100%; margin: 1.5rem 0; overflow: visible;
    }
    .qr {
      display: block; max-width: 100%;
      padding: clamp(0.75rem, 3vw, 1.25rem); border-radius: 22px;
      background: #ffffff; color: #111111; border: 1px solid var(--line);
      overflow: visible;
    }
    .qr svg {
      width: min(76vw, 340px); max-width: 100%; height: auto;
      display: block; overflow: visible;
    }
    a.button, button {
      display: inline-flex; align-items: center; justify-content: center;
      min-height: 44px; padding: 0.7rem 1.25rem; border: 0;
      border-radius: 999px; font: inherit; font-weight: 600;
      text-decoration: none; cursor: pointer;
    }
    a.button { min-height: 50px; background: var(--accent); color: var(--on-accent); }
    button { background: var(--secondary); color: var(--on-secondary); }
    a.button:focus-visible, button:focus-visible, summary:focus-visible,
    textarea:focus-visible {
      outline: 3px solid var(--accent); outline-offset: 3px;
    }
    .fallback {
      margin-top: 1.5rem; padding: 1rem 1.25rem;
      border-radius: 20px; background: var(--inset);
    }
    .fallback h2 { margin-top: 0; }
    dl {
      display: grid; grid-template-columns: max-content 1fr;
      gap: 0.45rem 1rem; margin: 1rem 0 0;
    }
    dt { color: var(--muted); }
    dd { margin: 0; overflow-wrap: anywhere; }
    code {
      font-family: ui-monospace, "SF Mono", Menlo, monospace;
      font-size: clamp(1rem, 4.4vw, 1.25rem); font-weight: 600;
      letter-spacing: 0.06em; white-space: nowrap;
    }
    ol.methods { margin: 1rem 0 1.5rem; padding-left: 1.5rem; }
    ol.methods li { margin: 0.7rem 0; }
    .warning {
      margin: 1.5rem 0; padding: 0.9rem 1.1rem; border-radius: 16px;
      background: var(--warn-bg); color: var(--warn-ink);
    }
    details { margin-top: 0.5rem; }
    summary {
      display: list-item; padding: 0.7rem 0;
      cursor: pointer; font-weight: 600;
    }
    label { display: block; margin-top: 1rem; font-weight: 600; }
    @media (max-width: 480px) {
      body { padding-left: 0.5rem; padding-right: 0.5rem; }
      main {
        width: 100%; margin: 0.5rem 0; padding: 1.25rem 1rem; border-radius: 22px;
      }
      .fallback { padding: 1rem; }
    }
    textarea {
      width: 100%; min-height: 6.5rem; margin-top: 0.5rem; padding: 0.8rem;
      border-radius: 14px; border: 1px solid var(--field);
      background: var(--inset); color: var(--ink); font: inherit;
    }
"""

# The HealthRelay mark, drawn from tools/generate_healthrelay_icon.py's 1000-unit
# geometry: gradient tile, white pulse, mint chevron.
_BRAND_MARK_SVG: Final = """<svg viewBox="0 0 1000 1000" aria-hidden="true">
      <defs><linearGradient id="hr-tile" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#0b3d4a"/><stop offset="1" stop-color="#07222e"/>
      </linearGradient></defs>
      <rect width="1000" height="1000" rx="225" fill="url(#hr-tile)"/>
      <g fill="none" stroke-width="44" stroke-linecap="round" stroke-linejoin="round">
        <polyline stroke="#ffffff"
          points="110,520 300,520 360,520 410,330 470,720 530,430 570,520 700,520"/>
        <polyline stroke="#5eead4" points="720,400 860,520 720,640"/>
      </g>
    </svg>"""


def _brand_header() -> str:
    return f'<div class="brand">{_BRAND_MARK_SVG}<span>HealthRelay</span></div>'


def render_pairing_setup_page(
    bundle: ReceiverPairingBundle | ReceiverPairingInvitationBundle,
    pairing_url: str,
) -> str:
    if isinstance(bundle, ReceiverPairingInvitationBundle):
        return _render_invitation_setup_page(bundle, pairing_url)
    return _render_legacy_setup_page(bundle, pairing_url)


def _render_invitation_setup_page(
    bundle: ReceiverPairingInvitationBundle,
    pairing_url: str,
) -> str:
    qr_svg = _pairing_qr_svg(pairing_url)
    escaped_label = html.escape(bundle.label, quote=True)
    escaped_receiver_url = html.escape(bundle.receiver_url, quote=True)
    escaped_code = html.escape(bundle.invitation_code, quote=True)
    escaped_expires_at = html.escape(bundle.expires_at, quote=True)
    escaped_warning = html.escape(bundle.warning, quote=True)
    escaped_pairing_url = html.escape(pairing_url, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{SETUP_PAGE_TITLE}</title>
  <style>{_SETUP_PAGE_STYLE}  </style>
</head>
<body>
  <main>
    {_brand_header()}
    <p class="eyebrow">Temporary invitation · single use</p>
    <h1>Connect this iPhone</h1>
    <h2>Scan with iPhone Camera</h2>
    <p>Open this page on a trusted screen, then scan the QR code.</p>
    <div class="qr-shell">
      <div class="qr" aria-label="Pairing QR code">{qr_svg}</div>
    </div>
    <p>
      <a class="button" href="{escaped_pairing_url}">Open in HealthRelay</a>
    </p>
    <section class="fallback">
      <h2>Use a code instead</h2>
      <p>In HealthRelay, choose <strong>Use a code instead</strong> and enter:</p>
      <dl>
        <dt>Device</dt><dd>{escaped_label}</dd>
        <dt>Server</dt><dd>{escaped_receiver_url}</dd>
        <dt>Code</dt><dd><code>{escaped_code}</code></dd>
        <dt>Expires</dt><dd>{escaped_expires_at}</dd>
      </dl>
    </section>
    <p class="warning">
      <strong>Private setup artifact.</strong> {escaped_warning}
      {SETUP_PAGE_DELETE_NOTICE}
    </p>
    <details>
      <summary>Show setup link</summary>
      <button type="button" onclick="copyPairingLink()">Copy setup link</button>
      <textarea id="pairing-url" readonly>{escaped_pairing_url}</textarea>
    </details>
  </main>
  <script>
    async function copyPairingLink() {{
      const field = document.getElementById('pairing-url');
      field.focus(); field.select();
      try {{ await navigator.clipboard.writeText(field.value); }}
      catch (_) {{ document.execCommand('copy'); }}
    }}
  </script>
</body>
</html>
"""


def _render_legacy_setup_page(
    bundle: ReceiverPairingBundle,
    pairing_url: str,
) -> str:
    qr_svg = _pairing_qr_svg(pairing_url)
    escaped_label = html.escape(bundle.label, quote=True)
    escaped_receiver_url = html.escape(bundle.receiver_url, quote=True)
    escaped_token_prefix = html.escape(bundle.token_prefix, quote=True)
    escaped_warning = html.escape(bundle.warning, quote=True)
    escaped_pairing_url = html.escape(pairing_url, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{SETUP_PAGE_TITLE}</title>
  <style>{_SETUP_PAGE_STYLE}  </style>
</head>
<body>
  <main>
    {_brand_header()}
    <h1>{SETUP_PAGE_TITLE}</h1>
    <p>
      Open this page only from a trusted screen or browser session. The setup link
      contains a receiver credential; keep it private and delete this file after
      pairing.
    </p>
    <h2>Recommended pairing methods</h2>
    <ol class="methods">
      <li>
        <strong>Best default:</strong> open this page on a trusted laptop, desktop,
        tablet, or another screen the iPhone can see, then scan the QR code with
        the iPhone Camera.
      </li>
      <li>
        <strong>Already on the iPhone:</strong> tap the button below to open
        HealthRelay directly.
      </li>
      <li>
        <strong>If browser handoff fails:</strong> copy the setup link and choose
        <em>Paste setup link in HealthRelay</em>.
      </li>
    </ol>
    <div class="qr-shell">
      <div class="qr" aria-label="Pairing QR code">{qr_svg}</div>
    </div>
    <p class="hint">
      The receiver URL must be reachable from the iPhone. Local machines, cloud
      hosts, LAN, and private networks can all work when routing/firewall
      settings allow it.
    </p>
    <p>
      <a class="button" href="{escaped_pairing_url}">
        Open HealthRelay
      </a>
    </p>
    <dl>
      <dt>Device label</dt><dd>{escaped_label}</dd>
      <dt>Receiver URL</dt><dd>{escaped_receiver_url}</dd>
      <dt>Token prefix</dt><dd>{escaped_token_prefix}</dd>
    </dl>
    <p class="warning">
      <strong>Secret setup file.</strong>
      {escaped_warning} {SETUP_PAGE_DELETE_NOTICE}
    </p>
    <label for="pairing-url">Direct link fallback</label>
    <button type="button" onclick="copyPairingLink()">
      Copy setup link
    </button>
    <textarea id="pairing-url" readonly>{escaped_pairing_url}</textarea>
  </main>
  <script>
    async function copyPairingLink() {{
      const field = document.getElementById('pairing-url');
      field.focus();
      field.select();
      try {{
        await navigator.clipboard.writeText(field.value);
      }} catch (_) {{
        document.execCommand('copy');
      }}
    }}
  </script>
</body>
</html>
"""


def _pairing_qr_svg(pairing_url: str) -> str:
    buffer = BytesIO()
    qr_code = segno.make(pairing_url, error="m")
    qr_code.save(
        buffer,
        kind="svg",
        scale=8,
        xmldecl=False,
        svgns=True,
        omitsize=True,
    )
    return buffer.getvalue().decode("utf-8")
