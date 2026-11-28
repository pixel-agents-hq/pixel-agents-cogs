// The Discord Activity bootstrap pixelagents adds to the Pixel Agents webview
// (`activity.html`). It runs the Embedded App SDK handshake and OAuth before
// the webview starts: the build turns the webview's module scripts into
// inert `text/x-pixel-agents-deferred` tags, and this releases them once the
// consuming cog's `start` hook agrees.
//
// The consuming cog serves `activity-config.js` next to `activity.html`; it
// runs first and sets `window.PIXEL_AGENTS_ACTIVITY` (see `ActivityConfig`).
// Every URL is relative to the page, so the page works under any Discord URL
// mapping target.

import { DiscordSDK } from '@discord/embedded-app-sdk';

type Scope = 'identify' | 'guilds' | 'guilds.members.read';

export interface ActivityContext {
  sdk: DiscordSDK;
  accessToken: string;
  // The token route's whole JSON answer (`access_token` plus whatever else
  // the consumer's server returns, e.g. its own session id).
  signIn: Record<string, unknown>;
  userId: string;
  guildId: string | null;
  channelId: string | null;
  instanceId: string;
}

export interface ActivityConfig {
  clientId: string;
  // Exchanges `{code}` for `{access_token, ...}` on the consuming cog's server.
  tokenUrl: string;
  scope?: Scope[];
  // Return false to keep the webview from starting (the hook showed why).
  start?: (context: ActivityContext) => boolean | void | Promise<boolean | void>;
}

declare global {
  interface Window {
    PIXEL_AGENTS_ACTIVITY?: ActivityConfig;
  }
}

const DEFERRED_TYPE = 'text/x-pixel-agents-deferred';
const NOTICE_ID = 'pixel-agents-activity-notice';

function notice(text: string): void {
  let note = document.getElementById(NOTICE_ID);
  if (note === null) {
    note = document.createElement('div');
    note.id = NOTICE_ID;
    note.setAttribute('role', 'alert');
    note.style.cssText =
      'position:fixed;inset:0;display:flex;align-items:center;justify-content:center;' +
      'padding:24px;text-align:center;z-index:2147483647;background:#1e1e2e;' +
      'color:#e6e6f0;font:16px/1.5 system-ui,sans-serif';
    (document.body ?? document.documentElement).appendChild(note);
  }
  note.textContent = text;
}

// Start the webview: each inert module tag becomes a real one.
function release(): void {
  for (const inert of Array.from(document.querySelectorAll(`script[type="${DEFERRED_TYPE}"]`))) {
    const script = document.createElement('script');
    script.type = 'module';
    const crossOrigin = inert.getAttribute('crossorigin');
    if (crossOrigin !== null) script.crossOrigin = crossOrigin;
    const src = inert.getAttribute('src');
    if (src !== null) script.src = new URL(src, document.baseURI).href;
    else script.textContent = inert.textContent;
    inert.replaceWith(script);
  }
}

// An Activity can't navigate away; Discord opens external links itself.
function openLinksThroughDiscord(sdk: DiscordSDK): void {
  const open = (raw: string): boolean => {
    let url: URL;
    try {
      url = new URL(raw, location.href);
    } catch {
      return false;
    }
    if ((url.protocol !== 'https:' && url.protocol !== 'http:') || url.origin === location.origin) {
      return false;
    }
    void sdk.commands.openExternalLink({ url: url.href }).catch(() => undefined);
    return true;
  };
  window.open = (target?: string | URL): null => {
    if (target !== undefined) open(String(target));
    return null;
  };
  document.addEventListener(
    'click',
    (event) => {
      const anchor = (event.target as Element | null)?.closest?.('a[href]');
      if (anchor instanceof HTMLAnchorElement && open(anchor.href)) event.preventDefault();
    },
    true,
  );
}

async function exchange(tokenUrl: string, code: string): Promise<Record<string, unknown>> {
  const response = await fetch(new URL(tokenUrl, location.href).href, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ code }),
  });
  const data = (response.ok ? await response.json() : null) as Record<string, unknown> | null;
  if (data === null || typeof data.access_token !== 'string' || !data.access_token) {
    throw new Error(`token exchange failed (${response.status})`);
  }
  return data;
}

async function main(): Promise<void> {
  const config = window.PIXEL_AGENTS_ACTIVITY;
  if (config === undefined || !config.clientId) {
    notice('This Activity is not set up on this bot.');
    return;
  }
  let sdk: DiscordSDK;
  try {
    sdk = new DiscordSDK(config.clientId);
  } catch {
    // No `frame_id`/`platform` query: this page was opened outside Discord.
    notice('This page runs inside Discord. Open it from the App Launcher.');
    return;
  }
  await sdk.ready();
  const { code } = await sdk.commands.authorize({
    client_id: config.clientId,
    response_type: 'code',
    state: '',
    prompt: 'none',
    scope: config.scope ?? ['identify'],
  });
  const signIn = await exchange(config.tokenUrl, code);
  const accessToken = signIn.access_token as string;
  const auth = await sdk.commands.authenticate({ access_token: accessToken });
  openLinksThroughDiscord(sdk);
  const context: ActivityContext = {
    sdk,
    accessToken,
    signIn,
    userId: auth.user.id,
    guildId: sdk.guildId,
    channelId: sdk.channelId,
    instanceId: sdk.instanceId,
  };
  if (config.start !== undefined && (await config.start(context)) === false) return;
  release();
}

main().catch((error: unknown) => {
  console.error('[pixel-agents activity]', error);
  notice('Pixel Agents could not start. Close the Activity and open it again.');
});
