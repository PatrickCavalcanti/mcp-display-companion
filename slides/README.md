# slides/

The `landscape-*.jpg` files here are **example photos** (the author's own, downsized
to 1384 px wide with all metadata stripped) so a fresh clone has something to show.
Replace or delete them freely.

Drop your photos here. That is the whole setup: the service lists this folder and
shows the images **full screen** on the panel's presentation page (the page to the
left of the clock), one every `slides.interval_s` seconds (20 by default), with a
crossfade.

- accepted: `.jpg` `.jpeg` `.png` `.webp` `.bmp` `.gif`
- order: by file name, or random with `"slides": { "shuffle": true }`
- a new photo appears within a minute, without restarting anything
- the page only exists while there is at least one image here
- the bundled `landscape-*.jpg` *are* versioned; anything else you put here is not
  (see `.gitignore`)

Another folder works too: point `slides.folder` in `config.json` at it (absolute
path, `~` or `%USERPROFILE%`).
