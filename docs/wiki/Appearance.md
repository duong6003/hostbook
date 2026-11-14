# Appearance

Open appearance settings directly:

```bash
hostbook settings
```

Or use `Ctrl-O` -> `Giao diện` in the app. Enter saves a previewed choice; Esc cancels.

## Layout and color

Choose two-line cards, a compact list, or a minimal name/address view. Borders,
separators, preview position, and icons can be adjusted independently. Ctrl-T switches
cards/compact; Ctrl-/ toggles the details pane. Preview hides when there is not enough
horizontal space.

Presets include cards, compact, minimal, Dracula, Nord, light, and high contrast.
Color themes also include Gruvbox, Solarized dark/light, and terminal ANSI colors.
Unicode and ASCII icons work without Nerd Fonts; the Nerd Font option requires a
compatible terminal font.

```bash
NO_COLOR=1 hostbook
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
list = cards              # cards, compact, minimal
border = rounded          # rounded, sharp, bold, double, none
preview = right           # right, bottom, hidden
separator = on            # on, off
icons = unicode           # unicode, ascii, nerd
theme = hostbook
key_ttl = 8h              # Agent key lifetime; hours, minutes, or seconds
connect_timeout = 15      # Seconds; HOSTBOOK_CONNECT_TIMEOUT takes precedence
```

Existing installs keep their saved choices. Invalid values fall back to defaults;
`hostbook doctor` reports the file and line to check. Configuration is parsed as data,
not executed as a shell script.

## Custom themes

Choose `Tự tạo theme…`. hostbook creates `themes/<name>.theme`, opens `$EDITOR`,
validates colors, and previews the result before use. Names support letters, numbers,
hyphens, and underscores.

Theme fields: `accent`, `title`, `dim`, `ok`, `warn`, `err`, `border`, `sel_bg`,
`chip_fg`, `chips`, and `base`. Colors accept ANSI names, indexes 0–255, or `#rrggbb`.
`base` is `dark`, `light`, or `16`. Keep every field; `chips` is a list of colors.
