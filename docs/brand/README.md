# Wipebot's logo

A bot whose screen shows a boss's health falling through a pull, in the website's navy and gold. The SVGs are the
sources; the PNGs are drawn from them at the sizes each place asks for.

| File | Where |
|---|---|
| `wipebot-icon.svg`, `wipebot-icon-1024.png`, `-512.png` | Discord app icon (1024 x 1024), avatars, the plugin |
| `wipebot-icon-light.svg`, `wipebot-icon-light-1024.png` | the same on the website's light paper |
| `wipebot-mark.svg` | the bot alone, transparent, for a page's header |
| `favicon.ico` (16 and 32 px), `favicon-16.png`, `favicon-32.png`, `apple-touch-icon.png` (180 px) | the website's icons |
| `discord-banner-680x240.png` | the Discord app's banner (the icon sits on its lower left, so the name is on the right) |
| `github-social-1280x640.png` | GitHub's social preview (Settings, General) |
| `og-image-1200x630.png`, `og-image-light-1200x630.png` | link previews (`og:image`) |

The server serves the 256 px icon at /icon.png (`src/wipebot/mcp/icon.png`) and names it as its icon; a website that
has its own /icon.png serves that instead.

Colours: navy `#0b101e`, gold `#f5c542` (`#a87a00` on light), paper `#f6f4ef`, ink `#1b2130`. The wordmark is
Inter Display ExtraBold, "Wipe" in ink and "bot" in gold, as on the website.
