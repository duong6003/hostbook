# Appearance

Open appearance settings directly:

```bash
hostbook settings
```

Or use `Ctrl-O` → **Appearance** (`Giao diện`) in the app. The menu lists **Presets**, **List style**,
**Frame**, **Detail pane**, **Card dividers**, **Icons**, **Colour theme**, **Language**, **Create your own
theme…** and **Reset to defaults** (Cards and the hostbook theme). Each choice previews live; Enter saves it
and Esc cancels.

## Layout and color

List style is **Two-line cards**, **One-line rows**, or **Name and IP only**. Frame
(rounded, sharp, bold, double, none), detail pane position (right, bottom, hidden), card dividers, and
icons can be adjusted independently. Ctrl-T switches cards/compact; Ctrl-/ toggles the details pane.
The preview hides when there is not enough horizontal space.

Presets: Cards (default), Compact, Minimal, Dracula, Nord compact, Light, High contrast.
Colour themes: hostbook, dracula, nord, gruvbox, solarized-dark, solarized-light, light,
high-contrast, and terminal (your terminal's ANSI colors).
Unicode and ASCII icons work without Nerd Fonts; the Nerd Font option requires a
compatible terminal font.

`NO_COLOR=1 hostbook` runs without color.

## Language

The interface is English or Vietnamese. It is asked on first run and changed in
**Appearance → Language** (`Language / Ngôn ngữ`); the choice is saved as `lang` in the config.
Resolution order: `HOSTBOOK_LANG` (`en` or `vi`, for one run), then `lang` in the config, then the
locale (`vi*` selects Vietnamese, anything else English). Installs that predate languages stay on
Vietnamese until you change it. Text coming from Git, SSH and other tools is shown as is, not translated.

```bash
HOSTBOOK_LANG=en hostbook
```

## Terminal backgrounds

hostbook inherits the terminal background in the list and preview. If your terminal
has a background image, it can show through; otherwise its ordinary background color
is used. The selection and project chips keep their own colors for readability.

Configure images, opacity, and blur in the terminal itself. hostbook does not need a
terminal-provider adapter or a separate image setting. Lower image brightness if text
becomes hard to read.

## Local configuration

The file is `${XDG_CONFIG_HOME:-$HOME/.config}/hostbook/config`. It is local to each
machine and is not synchronized with hosts.

```ini
lang = en                 # en, vi
list = cards              # cards, compact, minimal
border = rounded          # rounded, sharp, bold, double, none
preview = right           # right, bottom, hidden
separator = on            # on, off
icons = unicode           # unicode, ascii, nerd
theme = hostbook
key_ttl = 8h              # Agent key lifetime; hours, minutes, or seconds (HOSTBOOK_KEY_TTL, in seconds, overrides)
connect_timeout = 15      # Seconds; HOSTBOOK_CONNECT_TIMEOUT takes precedence
```

Existing installs keep their saved choices. Invalid values fall back to defaults;
`hostbook doctor` reports the file and line to check. Configuration is parsed as data,
not executed as a shell script.

## Custom themes

Choose **Create your own theme…** (`Tự tạo theme…`). hostbook creates `themes/<name>.theme`
from the current theme, opens `$EDITOR`, validates colors, and previews the result before use. Names support letters, numbers,
hyphens, and underscores.

Theme fields: `accent`, `title`, `dim`, `ok`, `warn`, `err`, `border`, `sel_bg`,
`chip_fg`, `chips`, and `base`. Colors accept ANSI names, indexes 0–255, or `#rrggbb`.
`base` is `dark`, `light`, or `16`. Keep every field; `chips` is a list of colors.
