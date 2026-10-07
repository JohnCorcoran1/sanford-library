# Sanford Library

A lightweight, single-user browser interface for browsing and maintaining a personal movie and television library. It does not require a database, account system, third-party Python packages, or build process. Optional TMDB integration makes adding movies and series faster.

## Run locally

The catalog is loaded from `movies.json` with `fetch`, so the site must be opened through a local web server rather than directly from the filesystem. The included server also enables the **Add title** button.

From the repository directory, run:

```powershell
python server.py
```

Then open [http://localhost:8000](http://localhost:8000) in a browser.

## Project structure

```text
sanford-library/
├── index.html                  # Interface, styling, filtering, and sorting
├── movies.json                 # Movie and series metadata
├── skins.json                  # Custom interface skins (created on first save)
├── server.py                   # Local server and catalog-writing API
├── assets/
│   ├── stevie-approved.png     # Shared family-approval badge
│   └── tmdb-logo.svg           # Required TMDB attribution
└── posters/
    └── *.jpg                   # Optimized poster images
```

## Configure TMDB search

1. Create a free TMDB account and request API access from [TMDB API settings](https://www.themoviedb.org/settings/api).
2. Copy the **API Read Access Token** (the long bearer token, not the shorter API key).
3. Create a `.env` file in the repository directory and add:

```text
TMDB_READ_TOKEN=your-api-read-access-token
```

4. Start the server:

```powershell
python server.py
```

The server loads `.env` automatically. An existing environment variable takes precedence. Keep this token private and do not add it to the repository; `.env` is ignored by Git. The browser calls the local Python server, which sends the token to TMDB; the token is never included in the page or catalog. If the token is not set, manual title entry remains available and TMDB search stays disabled.

## Add a title

1. Start the site with `TMDB_READ_TOKEN` configured as shown above.
2. Open [http://localhost:8000](http://localhost:8000).
3. Select **Add title**.
4. Search TMDB and select the matching movie or series. The title, category, duration, and poster are filled automatically.
5. Enter the format, HDR, audio, and Stevie Approved status for your copy, then submit the form.

You can edit the filled fields or choose a local poster to replace TMDB's image. You can also skip TMDB and enter everything manually; in that case, a local poster file is required.

The browser resizes the poster to fit within 640×960 and converts it to an optimized JPEG. The local server then saves the poster in `posters/` and adds the metadata to `movies.json`. Library order is assigned automatically. TMDB-backed entries also keep their `tmdbId` and `tmdbType` so the same title cannot be added twice from TMDB.

The server listens only on `127.0.0.1`, so catalog editing is available only from the computer running it.

## Edit the catalog manually

Edit `movies.json` to add or update titles. Each entry follows this shape:

```json
{
  "title": "Example Movie",
  "category": "Movie",
  "format": "UHD BD 4k",
  "hdr": "Dolby Vision",
  "audio": "Dolby Atmos",
  "family": true,
  "order": 117,
  "duration": "2h 10m",
  "poster": "posters/example-movie.jpg",
  "posterAlt": "Example Movie",
  "posterWidth": 640,
  "posterHeight": 960,
  "tmdbId": 12345,
  "tmdbType": "movie"
}
```

Guidelines:

- Keep `order` unique when using the default Library Order sort.
- Use `Movie` or `Series` for `category` to remain consistent with the current filters.
- Set `family` to `true` to display the shared Stevie Approved badge.
- Use an empty string for metadata that does not apply, such as `hdr`.
- Store posters in `posters/` and use forward slashes in JSON paths.
- Prefer JPEG posters no larger than 640×960 to keep the site fast.
- Keep `posterWidth` and `posterHeight` synchronized with the actual image dimensions.
- `tmdbId` and `tmdbType` are optional for manually maintained entries.

After editing the catalog, refresh the browser. If the JSON is malformed, the page will display a catalog-loading error; a JSON validator or editor with JSON validation can help locate the syntax problem.

## Customize the interface

Use the skin picker beside **Add title** to switch between the built-in Midnight, Matinee, and Deep Blue skins. The selected skin is remembered in that browser.

When the included server is running, select **Skins** to create a custom appearance. Custom skins are saved in `skins.json`, so they are available to every browser using this library. The editor controls colors, typography, spacing, content width, control and poster sizing, grid gaps, corner radii, shadows, header and family-badge images, the page background image, dialog backdrop, and scrollbars. Image fields accept a web URL or a path relative to the application, such as `assets/background.jpg`. Select a custom skin before opening the editor if you want to use it as a starting point or delete it. Arbitrary CSS is not accepted.

## Features

- Search across title, category, format, HDR, and audio fields
- Filter by category, format, HDR, audio, and family approval
- Sort by library order, title, format, category, HDR, or audio
- Adjustable poster size
- Lazy-loaded, independently cached poster images
- Responsive desktop and mobile layout
- Three built-in interface skins and server-persisted custom visual skins
- TMDB movie and series search with metadata and poster import
- Local form for manual entry and poster optimization

The site intentionally does not persist filters, sorting, or poster size between visits.

## Deployment

Deploy the entire directory to any static hosting service. No build command is required. Ensure the host serves `movies.json` as JSON and preserves the relative `assets/` and `posters/` paths.

Browsing and filtering work on a static host. The **Add title** button remains disabled unless the site is running through `server.py`, because a static host cannot write changes back to the catalog. TMDB search is also routed through `server.py` to keep the API token out of browser code.

## TMDB attribution

This product uses the TMDB API but is not endorsed or certified by TMDB. Movie and series metadata and poster artwork imported through search come from [The Movie Database](https://www.themoviedb.org/).
