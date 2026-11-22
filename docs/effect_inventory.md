# xLights effect inventory

Every effect in xLights 2026.14 (from the app's own `Contents/Resources/effectmetadata`,
which lists each effect's settings, ranges and defaults). Briefs can use any of the
**design effects** below through `looks` (see the README's *Design brief*); the generator
reads the installed app's metadata to turn a look's settings into the right
`E_SLIDER_` / `E_CHECKBOX_` / `E_CHOICE_` / `E_TEXTCTRL_` / `E_SPINCTRL_` keys and to
check names, ranges and choices.

Prop types are the generator's categories. In this show: **tree** = Mega Tree, Mega
Tree-2, Mini-Tree; **arch** = Arches-LEFT/RIGHT; **single_line** = MEGA BULB 1–6, JUMBO
Bulbs (floods); **custom** = snowflakes, ChromaTrees, ChromaWreath, G-Sphere, presents,
Santa Hat, the stars, Starburst xTreme; **other** = candy canes, MegaCane, porch columns,
Matrix, Santa Hat Matrix. "Matrix-like" means anything with a real 2D buffer: the
matrices, mega trees, G-Sphere, ChromaTrees, snowflakes and wreath.

Show profile notes: strobes and plain color washes are out (show rules); long stretches of
fast flashing are capped by the rules; faces only when a song's brief asks for them.

## Design effects

| Effect | What it looks like | Best on | Weak on | Feel |
|---|---|---|---|---|
| **Bars** | Bands of palette colors sliding up/down/out, optionally 3D or with a highlight | Mega trees, porch columns, matrices; "expand" on the G-Sphere | Single bulbs | Punchy (fast) or flowing (slow) |
| **Butterfly** | Swirling, organic color field | Matrix-like props, wreath, snowflakes | Lines, arches | Flowing |
| **Candle** | Flickering flame per node, wind-blown | Porch columns, mini trees, bulbs — warm nostalgic glow | Big matrices (reads as noise) | Calm, classic |
| **Circles** | Bouncing or radial circles/bubbles, plasma mode | Matrices, G-Sphere, mega trees | Lines | Playful |
| **Color Wash** | Whole prop fades between palette colors | Only with shimmer or a horizontal/vertical fade, as a transition | — plain washes are disliked | Background |
| **Curtain** | Opens/closes from an edge, with swag | Mega trees (top-down reveal), porch columns, arches (open from center) | Small props | Big reveals, intros/outros |
| **Fan** | Rotating fan blades from a center | Mega trees (from the top), G-Sphere, matrices | Lines | Punchy rotation |
| **Fill** | Prop fills up/down with color, bands, wrap | Porch columns, mega trees, candy canes, arches (fill-up builds) | — | Builds, risers |
| **Fire** | Rising flames | Porch columns, mega trees (base up), matrix | Snowflakes | Warm, dramatic |
| **Fireworks** | Bursting particles, can follow music | Mega trees, matrices, G-Sphere, Starburst | Lines | Big moments, finales |
| **Galaxy** | Spiral arm sweeping around a center | Mega trees from the top, G-Sphere, snowflakes, wreath | Lines | Flowing, hypnotic |
| **Garlands** | Draped garland shapes dropping in | Mega trees, porch columns, roof-like lines | Small props | Classic, nostalgic |
| **Kaleidoscope** | Mirrored, rotating pattern over the layer below | Matrices, G-Sphere (layer it over Plasma/Butterfly) | Lines | Modern |
| **Life** | Game-of-life cells | Matrices | Everything else | Modern, quirky |
| **Lightning** | Bolts, forked | Mega trees, matrices, porch columns | Small props | One-off hits |
| **Lines** | Bouncing lines with trails | Matrices, G-Sphere | Lines | Modern |
| **Liquid** | Fluid particle sim | Matrices | Everything else; heavy to render | Modern |
| **Marquee** | Theater-marquee bands running around | Presents, porch columns, matrix borders, candy canes | Trees | Classic, playful |
| **Meteors** | Streaks falling/rising/exploding/icicles | Mega trees (falling snow), arches, porch columns, icicle-like lines | Single bulbs | Flowing (slow) or punchy (explode) |
| **Morph** | A band of color moving/growing between two points | Arches, candy canes, porch columns, single lines; great accent | — | Smooth accent |
| **Music** | Bars/notes driven by the audio | Matrices, mega trees | Small props | Reactive |
| **On** | Solid color, optional ramp/shimmer/cycles | Floods (by rule), the arch chase steps, final hit | Long holds on heroes (looks like a plain wash) | Hits |
| **Pinwheel** | Rotating arms, 3D sweep | Mega trees, G-Sphere, snowflakes, wreath, Starburst | Lines | Playful, punchy |
| **Plasma** | Smooth flowing color field | Matrix-like props, mega trees | Lines | Flowing |
| **Ripple** | Shapes (circle, star, tree, candy cane, snowflake, heart…) rippling out/in | Mega trees, matrices, G-Sphere, snowflakes | Lines | Accents, themed shapes |
| **Shape** | Many shapes (stars, snowflakes, trees, hearts…) spawning and moving | Matrices, mega trees, G-Sphere | Lines | Playful, themed |
| **Shimmer** | Fast on/off glitter | Brief sparkle on anything | Long stretches (counts as fast flashing) | Sparkle accents |
| **Shockwave** | Expanding ring from a center | Mega trees, G-Sphere, snowflakes, wreath | Lines | Punchy hits |
| **SingleStrand** (Chase/Skips) | Chases, bounces, dual chases, skips along a line | Arches, candy canes, bulbs, porch columns | Matrices | Classic chases |
| **Snowflakes** | Drifting or accumulating snowflakes | Mega trees, matrices, G-Sphere | Lines | Calm, wintry |
| **Snowstorm** | Dense swirling snow streaks | Mega trees, matrices | Lines | Wintry, busier |
| **Spirals** | Barber-pole spirals, 3D, grow/shrink | Mega trees (the classic), porch columns, candy canes | Flat custom props | Classic; speed sets feel |
| **Spirograph** | Spirograph curves | Matrices, G-Sphere, wreath | Lines | Hypnotic |
| **Strobe** | Random strobing nodes | — strobes are disliked | — | — |
| **Tendril** | A moving tendril with trails | Matrices, mega trees | Lines | Organic, modern |
| **Tree** | Drawn tree with branches and lights | Mega trees, matrices | Everything else | Nostalgic |
| **Twinkle** | Random twinkling nodes | Stars, snowflakes, wreath, mini trees, bulbs — quiet parts | — | Calm, classic |
| **VU Meter** | Audio-reactive meters, spectrograms, shapes pulsing | Matrices, G-Sphere, mega trees | Small props | Modern, reactive |
| **Warp** | Water drops, dissolve, circle reveal, swirl over the layer below | Matrix-like props (as a transition) | Lines | Transitions |
| **Wave** | Sine/triangle/square/decaying waves | Mega trees, matrices, arches, porch columns | Single bulbs | Flowing |

## Utility and asset-driven effects

Not used by briefs: they need assets, hardware, MIDI or timing data the generator doesn't
supply, or they only modify other effects.

| Effect | Why not |
|---|---|
| Adjust | Edits channel values of other effects |
| DMX, Moving Head, Servo | Hardware channels (no such props in this show) |
| Duplicate | Copies another model's effects |
| Faces | Singing faces — placed by the singing rules, only when a brief turns faces on |
| Glediator, Pictures, Video, Shader, Sketch | Need files (images, video, shaders, sketches) |
| Guitar, Piano | Need MIDI tracks |
| Off | Blanks a layer (used structurally, not as a look) |
| State | Needs state definitions on the model |
| Text | Text/lyrics on a matrix — possible later for lyric cues on the Matrix |
