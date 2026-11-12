# Changelog

## 0.2.2

- Server mode, sharing: everyone and each group are ticked in a list (every group of the accounts), with
  their right next to them; users are still added by name. For a connection the rights read *Use* and *Use and
  modify*.
- Accounts window: the leftover sentence about directory accounts is replaced by how groups work; the server
  guide says the same.
- Server guide: *Mettre à jour* gives the update procedure that keeps flows, accounts and settings (backup of the
  `carriole` volume, new `compose.yaml`, `docker compose pull && docker compose up -d`) and the way back (restore,
  pinned version); checked from 0.2.1. Local changes go in `compose.override.yaml` and `.env` (`COMPOSE_PROFILES`
  for PostgreSQL), so `compose.yaml` can be replaced at each update.
- Docker: the image's user has a fixed group, 10001 like its uid (it was a system group); volumes of
  earlier versions keep working.
- Server mode: the first start refuses `ETL_ADMIN_PASSWORD` when it still holds the value of `carriole.env.example`,
  which is public; no account is created with it.
- Server mode, Docker: `CARRIOLE_BIND` chooses the listened address (default `127.0.0.1`; `0.0.0.0` opens the port to
  the network without editing `compose.yaml`); the data folder is mounted `rslave`, so a share mounted on demand
  (autofs, `x-systemd.automount`) stays visible. The server guide explains the `.env` file of Compose and how to give
  a Windows (SMB) share to the image's user.
- A flow file (`.json`) dragged into the window opens, like *Open…*: by its path in the desktop application, as a new
  flow to save in the browser (the API reads it without storing it, old modifier names included).
- Server mode: the bubblewrap sandbox of the Python code of flows works when the venv's Python is a link to a Python
  installed elsewhere (uv), and inside a container, which forbids mounting a new `/proc` (it starts without one).
  The server guide gives the two container options that allow it.

## 0.2.1

- Server mode: each release publishes the server image to `ghcr.io/tlecae/carriole` (version and `latest`); on any
  Linux server, `compose.yaml` and `carriole.env` are enough: `docker compose pull && docker compose up -d`. The
  README describes the installation; `CARRIOLE_VERSION` pins a version, `CARRIOLE_PORT` changes the port.
- Server mode: administrators create, edit and delete accounts in *Tools → Accounts* (name, groups, new password);
  each user changes their own password from their menu. `ETL_ADMIN_PASSWORD` creates the first administrator account
  on a server without any ([ADR 0058](docs/decisions/0058-comptes-locaux.md),
  [ADR 0059](docs/decisions/0059-comptes-locaux-seulement.md)).
- Server mode: Docker image (`Dockerfile`: Carriole, its interface, DuckDB extensions, PROJ grids and bubblewrap,
  unprivileged user, `/var/lib/carriole` volume) and an example in `docker/compose.yaml`, its settings in a local
  `docker/carriole.env` copied from `docker/carriole.env.example`, PostgreSQL as an optional profile. A CI job builds
  the image, checks that an identity is required and that the Python code of flows is isolated inside the container. An
  unusable bubblewrap is reported with its reason at analysis.
- Server mode: the metadata (flows, runs, sessions, shares, activity log) can live in PostgreSQL (`ETL_DB_URL` or
  `ETL_DB_URL_FILE`); the store tests and the server scenarios also run on a disposable PostgreSQL, locally and in a
  CI job. Text fields are bounded to their column size ([ADR 0057](docs/decisions/0057-metadonnees-postgresql.md)).
- Server mode: activity log for administrators (*Tools → Activity log*): sign-ins and refused sign-ins, writes on
  flows, connections, runs, schedules and settings, with user, date and address, never a password or flow content;
  kept `ETL_AUDIT_DAYS` days ([ADR 0056](docs/decisions/0056-journal-d-activite.md)). Metadata database schema v11
  (`audit`).
- Server mode: a fair queue lets through first the run of the user with the fewest runs in progress, so a long
  batch no longer holds everyone back; optional quotas of memory and cores per user, set in *Settings →
  Computation* ([ADR 0055](docs/decisions/0055-file-equitable-et-quotas.md)).
- Server mode: flows, runs and connections are private to their owner until shared, read or write, with a user, a
  group or everyone (*Share…* on the home cards, in the *File* menu and in *Connections*). A flow shared read-only
  opens with a "read-only" tag and *Save* makes a copy. Connections are checked when the run is created and again
  by the worker for the run's owner ([ADR 0054](docs/decisions/0054-droits-et-partage.md)). Metadata database schema
  v10 (`shares`).
- Server mode: the Python code of a flow (*PythonScript*) runs in an isolated process (bubblewrap): read-only system,
  no data, database, network or server variables; `ETL_USER_CODE` can also deny it. On a computer nothing changes
  ([ADR 0053](docs/decisions/0053-code-python-isole.md)).
- Server mode: only administrators change the machine settings (*Settings*, assistant providers), install a
  modifier written by the assistant and stop the server; other users see the settings read-only. Each user keeps
  their own interface language. *Settings → Server* shows how the server is set.
- Server mode (`etl serve --server`): several users work in their browser on a shared machine. Each one signs in
  with a local account (scrypt hashes, also managed by `etl users`). Sessions in an `HttpOnly; SameSite=Strict`
  cookie, password attempts slowed down, administrators set by `ETL_ADMINS`; without allowed folders, no file is
  accessible. Login page and user menu in the interface
  ([ADR 0052](docs/decisions/0052-mode-serveur-et-authentification.md), [server guide](docs/serveur.md)). Metadata
  database schema v9 (`sessions`, `users`).
- Desktop app: closing the window with unsaved changes asks whether to close without saving (the close button did
  nothing). Quitting from the menu or the tray asks before stopping the engine.
- Canvas: links between nodes are near white in the dark theme (they were grey, hard to see).
- DXF (AutoCAD): *DxfReader* reads the entities of a drawing (layer, line type, text; blocks exploded; closed
  polylines as polygons; layers to read; coordinate system to declare). *DxfWriter* writes each geometry on the
  layer given by an attribute, points with a text as TEXT entities, polygons as closed polylines or hatches; the
  other attributes are not written (a DXF has no attribute table) and the log names them.
- Writing into an existing layer or table: attributes it cannot receive are a flow error, shown on the node, and
  the run stops before reading any data (GDAL used to stop on "Cannot find OGR field" after reading). File
  writers in `append` mode (GeoPackage, SpatiaLite, Shapefile, GeoJSON, FlatGeobuf, MapInfo, CSV, Excel) list the
  incoming attributes against the columns of the layer, the missing ones in red, as database writers already did;
  database writers (`append`, `truncate`, `upsert`) are now checked before the run too.
- CSV and Excel appends follow the header of the existing file or sheet: each attribute goes to its column
  (they were written by position). Appending to a missing CSV writes its header.
- *GeoParquetWriter*: `append` adds to the file instead of replacing it (its rows are rewritten, then the new
  ones, arranged by its columns; a different coordinate system is refused), `create` refuses an existing file,
  and a partitioned append adds a file per partition instead of overwriting the previous one.
- Proposed values (tables of a database, attributes, coordinate systems, AI models) open in a list with the
  colours of the application instead of the system list.

## 0.1.7

- Site: home page reorganized for GIS users who do not code: number of modifiers by category (read from the
  published catalogue), formats by family, build / check / automate, video and screenshots; the Python section is
  reduced to one line.
- *OverlayPolygons*: new *Attribute values* option. `list` keeps one list per attribute (as before); `first` /
  `last` give a single value, the first or last one filled in, in arrival order. Overlaying two layers (municipalities
  and land cover) gives each piece the values of both, ready to group by them.
- Modifiers sized from the memory budget of *Settings → Computation* instead of fixed caps
  ([ADR 0045](docs/decisions/0045-conventions-des-modificateurs.md)): *CreateGrid* produces its cells by slices on all
  cores (no 5 million cell limit; only a grid that would not fit on the disk is refused); *Dissolve* packs groups
  by bytes of geometry; *Contour* and *Polygonize* accept rasters as large as the budget allows. Raster windows use
  the cores of the run.
- *JoinByLocation*: the list of related values is built without a loop per feature. *Counter* with groups has an
  SQL route (computed by DuckDB, ports defined on demand).
- *StatisticsByGroup*: the `summary` port (one row per group) comes first, so the inspector shows it rather than all
  the features; saved flows are unchanged (links name their ports).
- Runs can be paused and resumed (*Pause* / *Resume* next to *Stop*, and the *Run* menu): the run stops at the next
  safe point (between batches or nodes), keeps its memory and results, frees the processor, and resumes where it
  was, even in the middle of a node; its time limit does not count the pause. Nodes stopped at the safe point show
  "paused" on the canvas, in the inspector, the run list and the home page. If the flow was changed during the
  pause, *Resume* stops the run and starts it again: finished nodes that did not change are taken from the cache,
  changed nodes and everything downstream are recomputed
  ([ADR 0051](docs/decisions/0051-pause-d-un-run.md)). Metadata database schema v8 (`runs.paused`).
- Desktop: after an update, the window showed the previous interface and the map failed to load ("Failed to fetch
  dynamically imported module"): the page was kept in the browser cache. The interface is now served with cache
  headers (`index.html` revalidated, hashed assets kept) and the application clears its cache at start. In
  development, `npm start` restarts an engine left running in the background when the Python sources changed
  since it started (unless a run is in progress).
- Map: a layer coloured by a text attribute shows its 20 most frequent values in colour (10 before), the others in
  grey; the grey of the ninth colour, too close to "Other values", is replaced.
- Editor: the logo is removed from the top bar (the window title shows the name); a home button returns to the flow
  list.
- New example flow, first on the home page: monthly flight delays from a 10 million row Parquet file read over https.
- *Settings → Computation → Engine cores*: the number of cores the engine uses, 60 % of the computer's cores by
  default. Every parallel computation follows it (share of each run, DuckDB, batches and nodes in parallel, Arrow,
  GDAL, rasters, inspector); the fixed limits of 8 batches and 8 nodes are gone, and a heavy node alone in its run
  uses all the run's cores. The panel shows how the cores are shared and how to choose
  ([ADR 0050](docs/decisions/0050-budget-de-coeurs.md)).
- Blocking modifiers (*Dissolve*, *OverlayPolygons*, joins, sorts…): an input larger than their share of the memory
  budget spills to disk (memory-mapped Arrow files in the run's spill folder) instead of exhausting memory.
- *FindDuplicates*: on the batch route, the keys already seen are kept in DuckDB (within the budget, spilled to disk)
  instead of a Python set. *SampleRasterValues* and *Rasterize*: the fixed window and size limits follow the memory
  budget and the free disk space.

## 0.1.6

- *OverlayPolygons*: computed by tiles on all cores, sized from the memory budget of *Settings* and the number
  of cores; pieces across tiles are joined exactly. A polygon with a very large extent (a network in one piece) no
  longer keeps the whole computation on one core: 184 s to 32 s on 177,000 land-cover polygons
  ([ADR 0049](docs/decisions/0049-superposition-par-carreaux.md)).
- An input connected to several outputs has the attributes of all of them (rows joined by name, missing columns
  empty): only the schema of one link was used, so an attribute of another was reported missing and stopped the
  run; on the batch route, each batch is now conformed to the joined schema, as the SQL route does.
- Expressions with spatial functions (`ST_Area`, `ST_Buffer`…) work again on data read by GDAL (GeoJSON,
  GeoPackage, Shapefile…) or from a GeoParquet whose geometry carries the Arrow `geoarrow.wkb` extension: DuckDB 2
  read such a column as a native geometry and the expression failed. The geometry is now plain WKB everywhere.
- Canvas: fitting the view (opening a flow, first node, button) no longer zooms in beyond 100 %.
- README and site: new screenshots and a one-minute video of a flow built, run and inspected, recorded from the
  application by `scripts/site_media.ps1`; plainer wording. In English, the OpenStreetMap basemap is labelled
  "Streets".
- Inspector: the table of a dataset with a date-and-time-with-time-zone field (such as a GeoParquet read over https)
  is shown again; it failed with "invalid query" because DuckDB needs `pytz` for these values, now a dependency.
- Appearance follows the system light or dark theme; *Settings → General → Appearance* can force light or dark.
- Editor: with nothing selected, the properties panel folds into a narrow strip; *File → Flow properties* or the strip
  reopens the flow's description, published parameters and variables.
- Forms: the help of each field is in an info tooltip next to its label; for built-in modifiers it links to the
  parameter's row in the catalogue on GitHub, and the node panel links to the modifier's section.
- *Manage attributes* (`RefactorAttributes`): a table of all incoming attributes (input, output, value, action), which
  can be enlarged in an overlay; rules whose order matters stay as a list.
- SQL editor: SQL parameters of the database modifiers and `ExecuteSql` open an overlay with the database dialect,
  completion of tables and columns, and errors underlined as you type (DuckDB parsing, or the PostgreSQL query plan
  in a read-only transaction).
- CSV and (Geo)Parquet readers: *Filter (WHERE)*, a DuckDB condition applied while reading; DuckDB reads only the
  useful columns and row groups.
- Database writers (append, truncate, upsert): incoming attributes missing from the existing table are shown in red
  before the run.
- Home: an *Examples* section opens the Insee Melodi populations and Sentinel-2 NDVI (STAC) flows as new flows; they
  are now shipped with the application.
- *SplitToFiles*: no limit on the number of files (it stopped beyond 200).
- GeoParquet: the CRS is visible to readers that use the Arrow schema of the file (GDAL, QGIS, geopandas).
- Inspector map: a simplified merged polygon whose rings cross no longer draws filled rectangles outside the data
  ([ADR 0046](docs/decisions/0046-carte-polygones-fusionnes-decoupes.md)); clicking a feature on the map no longer
  moves the map; the layers menu is no longer covered by the minimap button.
- Editors: the SQL and expression editor has a side panel with the attributes (tables and columns of the database,
  inputs of the node, columns of the file a reader reads) and their most frequent values, inserted at the cursor in
  a click; it opens from every expression field and from the values of *Manage attributes*.
- SQL editor of a database: tables are listed under their schema, folded when there are several; opening a table
  shows its columns with their types.
- CSV and (Geo)Parquet readers: *Attributes read (SELECT)*, the columns kept when reading (DuckDB reads only them
  and those of the filter); with the filter, it is shown as a single *Read query* field, which opens an overlay
  showing SELECT, FROM, WHERE and LIMIT, with the file's columns to check, their values, and a count of the
  features the filter keeps.
- Run parameters: the launch dialog proposes the values of the last run; going back to the defaults changed the
  fingerprint of the nodes using a parameter, so the cache was not used.
- Inspector map: the selection of a huge entity is read at the level of the tiles of the current zoom, within the
  displayed extent; it was generalized over the whole entity, coarse once zoomed.
- Cache: database, STAC and web API (GET) reads are reused while their settings and upstream do not change, so a
  flow run again with the same parameters reads nothing again; *Run everything again* reads them
  ([ADR 0048](docs/decisions/0048-sources-externes-reprises-du-cache.md)).

## 0.1.5

- Runs: a long computation is no longer declared *interrupted* while its worker is still running. A computation that
  holds Python's GIL (such as polygonizing a large batch) stopped the worker's heartbeat for more than 30 s; another
  worker then marked the run interrupted, and the editor kept showing the node as running although the run went on
  and finished. A run without heartbeat is now interrupted only if its worker process is gone (same computer).
- *OverlayPolygons*: the faces of each batch are built by DuckDB, which releases the GIL (same faces, same order):
  the other batches keep working in parallel meanwhile.
- Editor: when a run ends without a recorded result (worker stopped), its nodes still shown as running are marked
  cancelled instead of spinning forever.
- Release: the Windows installer can be signed by SignPath Foundation (free code signing for open source projects)
  once the project is accepted there; each signing request is approved by hand
  ([ADR 0047](docs/decisions/0047-signature-installateur-windows.md)). Workflow artifacts are kept one day.
- README: a privacy section lists what Carriole sends over the network, and when.

## 0.1.4

- Canvas: two nodes are connected by clicking a port, or the *Connect* button of the node bar, then the other node.
  A free node dropped on a link is inserted into it (one undo step). The chevron of an output port lists its
  attributes and their types. Notes can be linked to a node (node bar or note bar): they follow it when it moves, are
  copied and deleted with it, and keep the link in the flow file (`node` field of the annotation). The overview map
  can be folded.
- Inspector map: always seen from above, north up (no rotation or tilt). The *Map* checkbox stays visible when the
  map is hidden, so it can be shown again. *Export* and *Layers* (formerly *Overlay*) are drop-down menus; *Colour*
  and *Layers* sit on the map, next to the basemap buttons.
- Inspector table: a table of more than about a million rows scrolls to its end, and a feature clicked on the map is
  found at any row. The browser caps the height of an element (around 33 million pixels, less when zoomed in), so
  beyond that cap the scrollbar position is scaled: wheel and keyboard still move row by row.
- Inspector: a `fid` column of the data (PostgreSQL table, GeoPackage…) no longer hides the feature number. It
  is shown as `fid_data`; before, rows far down the table stayed empty, sorting or filtering failed, and the export
  replaced that column with the feature numbers. The export writes it back as `fid`.
- Example flows in `examples/flows`: municipal populations from the Insee Melodi API joined to the outlines of the
  municipalities, and the NDVI of a municipality from a Sentinel-2 image found in a STAC catalog.
- *HttpRequest*: a *Maximum response size* parameter (50 MB). A larger response used to be cut at 5 MB without
  warning, which left unreadable JSON; such a feature now leaves on *Rejected* with the reason. An empty batch no
  longer stops the run.
- *CreateFeatures*: a feature without attributes or geometry, used to trigger a call, is no longer lost (it keeps
  `_index`).
- *STACSearch*: a search on the received geometries no longer fails ("module 'shapely' has no attribute 'ops'").
- Rasters: the GDAL block cache is 512 MB, as intended, instead of 512 bytes. A reduced read decompressed each
  block hundreds of times: the inspector preview of a 2 million pixel NDVI took 30 s, it takes 0.2 s. The raster
  settings panel stays within the map.
- Inspector: the *Statistics* button is replaced by a button in each column header, which computes the statistics
  of that field only (over the whole port). Computing every field at once took several minutes on large ports or
  stopped with "query too long"; one field reads a single column. A second click hides them or cancels the
  computation, and switching to another port stops it on the server. Errors of the inspector are shown in a banner
  above the table.
- Node colours: each built-in category has its own, clearly distinct colour; the palette and the nodes use the same
  colour in every language.
- Run log: a header (nodes to run, nodes taken from the cache, memory and cores), the start and end of each node
  with its duration and its counts per port, rejected features, disabled nodes, the error of a failed node in one
  line, and a summary at the end of the run.
- Dependencies: Electron 45, and the Python, npm and GitHub Actions dependencies updated (Dependabot updates are
  grouped, major versions apart).

## 0.1.3

- Cache: running a flow up to a node no longer hides the results of the other branches. They stay viewable and are
  reused by the next run, including nodes that a full run always re-runs (web requests, launched flows) as long as
  nothing changed in them. Raster nodes taken from the cache keep working once the previous run is cleaned up.
- Nodes taken from the cache are available as soon as a run starts, even while other nodes are still running.
- A writer that has finished no longer stays shown as running: the run's recorded result is applied when the run ends
  and when it is reopened.
- Faster reading: (Geo)Parquet and CSV files are read by DuckDB on all cores and written straight to the cache. The
  cache files of every node are encoded in a background thread, with dictionary encoding for text columns only.
  The (Geo)Parquet writer no longer loops over every feature. A file's schema is read again only when the file
  changes (GDAL reads a whole GeoJSON file to give it, at every change in the editor).
- Command line: without a plugins folder, the Python scripts of the current folder were imported as plugins; they no
  longer are.
- CSV reader with X/Y columns: a missing coordinate gives no geometry (it gave an empty point).
- *(Geo)Parquet reader*: a single local file that needs no change (no added file name, no row limit, geometry, if
  any, already in a `geometry` column) is no longer copied into the cache: the node's result points to the file.
  Reading a 4 GB file now takes a fraction of a second instead of over 20 minutes. If the file changes after the run,
  run the flow again to view it.
- Filters and attribute operations no longer write their results during the run: each output keeps its query, and a
  chain of such nodes runs as one DuckDB query, read only by the node that needs the data, or by the inspector when the
  output is opened ("Preparing data…" the first time). Counts and rejected features are unchanged: a value that fails
  sends the node back to batch processing, which rejects exactly the features at fault. Covered: *Extract by
  expression*, *Multi-output filter*, *Create attributes*, *Manage attributes*, *Convert attribute types*, *Keep*,
  *Remove* and *Rename attributes*, *Rename in bulk*.
- *Statistics by group*: the features with the statistics of their group are no longer sorted with all their columns
  to keep their order; only the group keys are. On wide tables this used to spill to disk until it was full
  ("not enough space on the disk") or run out of memory: 1 million rows × 200 columns, out of memory before, 94 s now.
- An output of a sort, statistics or join node that was only counted in a previous run (nothing read it) is computed
  and written once when a node now reads it, instead of being recomputed by every node that reads it.
- A full disk, during a run or in the inspector, gives a clear message: the drive, its free space and what to do.
  It no longer sends the node to batch processing (which would fail the same way).
- Editor: after a run, changing a node (parameters, enabled or not, links, a published parameter it uses) marks it
  and every node downstream as "to run again": dashed outline, greyed counts, and a note in the inspector above
  their data. Renaming a node does not. The marks go away with the next run.
- Properties panel: its width can be changed by dragging its left edge (or with the left and right arrow keys), and is
  remembered. A node with more than 6 parameters gets a search box (label, name, description and proposed values,
  ignoring case and accents). Multiple choices are check boxes: attributes (with a filter for long lists, "all" /
  "none", the order of the checks kept, an attribute missing upstream still shown and marked) and lists of values
  such as the statistics to compute, which used to be typed by hand.
- No more per-feature Python loops in: *Attribute to geometry* (a column of unreadable values: 3 times faster; the
  exact reason is given for the first 100 different values of each batch, the format for the others), *Mapping
  table* (was quadratic in the number of rejected features), *Repair geometries*, *Create a point* (text
  coordinates), *Split text*, *Extract text* (before/after), *Explode a list*, *Counter* and *Duplicates* (by group),
  *Sample raster values*, *Create features*, raster outputs, file names added by readers, rejected features.
- Features in error in an expression (*Create attributes*, *Convert attribute types*, *Convert dates*…) are found 2
  to 4 times faster: only the rows that can be in error are examined, in one table registered once.
- *Create attributes* with a type: the expression is evaluated once instead of twice. *Web request*: one connection
  and one rate limiter for the whole run (the maximum rate now holds across batches). *List files*: the folder is
  walked once. *Dissolve*: grouping small groups is no longer quadratic. Coordinate transformations are reused by
  every raster modifier.
- *Mapping table*: a boolean value is compared as `true`/`false` (it was `True`/`False`). A missing attribute is
  reported before the run reads any data.
- *Explode a list*: a list that is the only attribute no longer gives an error; an existing index attribute is
  replaced instead of being added a second time.
- Same message everywhere for a missing attribute: "missing attribute: a" or "missing attributes: a, b" (some
  modifiers printed a Python list).
- *Web request*: features whose call fails now leave on "Rejected" with the reason in `_rejection_reason`, like
  everywhere else (they left on "Failed" with `_http_error`). Saved flows are reconnected to "Rejected" when opened.
- Renamed parameters, migrated when a saved flow is opened: *Mapping table* "Attribute to translate" (`source` →
  `attribute`), *Geometry to attribute* "Remove the geometry" (`remove` → `remove_geometry`).
- More configuration errors are reported before the run reads any data: missing attributes of *Counter*,
  *Duplicates*, *Sort*, *Create a point*, *Dissolve*, *Hull*, *Set variables*, *Pivot wider*, *Buffer*, missing keys
  of *Join by attribute*, a geographic coordinate system for *Buffer*, *Measure area* and *Measure length*.
- *Measure area* and *Measure length*: a feature without geometry is rejected as "no geometry" (it said "not a
  polygon" / "not a line"); *Measure length* accepts rings. *Nearest neighbour* with no candidate rejects the
  features without geometry like it does otherwise.
- *Create attributes*: new "On failure" option, "null" leaves the attribute empty instead of rejecting the feature.
  *Extract JSON*: new "Invalid JSON" option, "reject" sends the feature to "Rejected" (default unchanged: empty
  values).
- *Sort*, *Duplicates*, *Counter* (without groups), *Replace empty values* (text attributes), *Explode a list*,
  *Build a list* and *SQL query* run in DuckDB: written ports spill to disk instead of running out of memory, and a chain of
  attribute operations stays one query. *Sort* gives the same order whichever way it runs (text of numbers,
  spaces, NaN). A sum of integers in a *SQL query* is a decimal number on both ways.
- *Sample*, *Run a flow*, *Nearest neighbour* and *Rasterize* count among the nodes that load all their input (a
  few at a time in a run). Spatial joins and filters, points in polygons and raster sampling work by slices of
  20 000 features. *Simplify* only re-encodes the geometries it simplified.
- Conventions shared by all modifiers, applied so far and still to apply: [ADR 0045](docs/decisions/0045-conventions-des-modificateurs.md).
- *Replace text*, *Mapping table* (except "reject") and *Extract JSON* on text attributes run in DuckDB like the
  other attribute operations: nothing is written during the run, and a chain of such nodes is one query. *Extract
  JSON* no longer opens a DuckDB connection per batch.
- *Settings → Compute → Cache folder*: the folder for run results and for the temporary data of computations larger
  than the memory budget can be moved to another local disk.
- Memory: a sort, statistics or join node that is the only one of its kind in a run gets DuckDB's whole share of the
  memory budget (it got half of it). Batch sizes follow the budget set in *Settings* instead of a fixed 64 MB, also
  when reading a Parquet file with wide rows.
- Sort, statistics and attribute join: an output that no node reads is counted but not computed during the run. It
  is computed when it is opened in the inspector. Example: statistics by municipality on a 13.6 million row file,
  where only the summary is used, took over 10 minutes and now take 8 s.
- Inspector: a preparation that is no longer displayed, because another port or node was chosen, stops after a few
  seconds. This now includes choosing a node that has no data yet (being computed, or not run): the panel used to
  show "Preparing data…" for the previous node next to the message about the new one, and kept preparing it.
- Inspector: while a run computes the selected node, the inspector shows "… is being computed". It used to show an
  upstream node taken from the cache, or the node's previous data, as if the run had finished.
- User expressions evaluated by DuckDB on the data of a run reach no other file than that data.
- Wide tables (hundreds of text columns): DuckDB uses fewer threads and smaller blocks so that it stays within its
  memory share instead of spilling to disk (a filter on 1 million rows of 200 columns: out of memory before, 11 s now).
- Caching off: an unconnected output of a node whose other outputs are used (the *Failed* output of a filter…) is
  counted but no longer written. A filter on a 13 million row file: 250 s → 44 s.
- *(Geo)Parquet reader* and *(Geo)Parquet writer*: new names. Parquet files without geometry were already supported.
- Merged modifiers, old flows are converted when opened:
  - *Lookup join* into *Attribute join*, which gains a choice of the result geometry (that of the joined features
    keeps their coordinate system);
  - *Count points in areas* into *Spatial relator*, which gains sums and a *first* option, and keeps in memory only
    the smaller of its two inputs;
  - the *Repair* option of *Validate geometries* is removed: *Repair geometries* does it.
- DuckDB 2.0.0 (LTS). Flows give the same results as with 1.5: an empty (`NULL`) attribute keeps a type and can
  be written to GeoPackage, `//` and `%` by zero give NULL, `~` matches the whole value. Lambdas are written
  `lambda x : x + 1`; the old `x -> x + 1` form is no longer accepted.
- GeoParquet reader on the web: with *Maximum rows read*, DuckDB 2.0's read-ahead is turned off, so only the needed
  parts of the file are downloaded.
- Windows: closing Carriole could leave the background engine (and its workers) running until the next start, when a
  connection had been cut abruptly; the engine's shutdown now always ends within a few seconds.
- Inspector map: huge polygons (a *Dissolve* or merged *Buffer* output, one piece with thousands of holes) no longer
  keep the map from showing. GEOS validated and repaired every simplified polygon: more than 5 minutes per zoom level
  for a 1.6-million-vertex polygon. Their rings are now simplified as lines, and the polygon is cut into pieces on the
  tile grid, with its outline drawn apart so the cuts do not show. Its map is ready in seconds. The map is prepared
  on all cores, and a tile that fails is reported on the map instead of leaving an empty area.
- Inspector map: "Loading the map…" no longer stays forever when the basemap cannot be reached (offline,
  proxy): it only waits for the port's data. It is shown at the bottom center, no longer under the basemap switch.
- Inspector: clicking a feature on the map finds its row in the table without sorting the whole table (it counts the
  rows before it): 0.3 s instead of 4 s on 15 million points sorted by a text column. A page of a sorted or filtered
  table sorts only the keys, then reads its own rows. A message shows while the row is being found, and these
  queries may run for up to 2 minutes. Clicking a feature no longer opens a popup: its attributes are in the table.
- Inspector map: a *Legend* button under the zoom buttons, shown when several layers are displayed or the layer is
  colored by an attribute. A numeric attribute is colored by five equal-count classes (quantiles of the filtered
  rows) instead of a linear ramp between its minimum and maximum; other attributes keep one color per frequent value.
- Inspector map: changing the color attribute or the table filters while the map was shown could leave the previous
  tiles on screen (features drawn grey, or not filtered). MapLibre ignored the new tile address while the layer
  was being restyled, and kept the old data of tiles still loading; the new address is now applied once the layer
  has loaded.
- Windows installer: the engine did not start on a computer without the Visual C++ Redistributable (`numexpr` needs
  `msvcp140.dll`). The Visual C++ runtime DLLs are now shipped next to the embedded Python. When the engine fails to
  start, the error message gives the Python error from its log and the full path of the log.
- *Spatial relator* retrieves attributes of the related features, like the attribute join: attributes to retrieve,
  prefix, name conflict, and for several related features either those of the first one, one "base" feature per
  related feature, or the list of the values of each attribute. Without a related feature they stay empty.
- Editor: in the *Spatial relator* and the *Attribute join*, an attribute list only offers the attributes of its own
  input ("related", "join" or "base"), so *All* no longer selects attributes of the other input. The analysis no
  longer reports an attribute of "related" as missing upstream.
- *Rename attributes* and *Refactor attributes* lost the coordinate reference system of the geometry: no map in the
  inspector, and a file written afterwards had no projection (a *Reproject* was needed). They keep it now, and
  their cached outputs are recomputed. As a safeguard for every modifier, plugins included, an output whose
  geometry has no CRS takes the CRS of its inputs when they share one.
- Windows: the engine no longer refuses to start when its encryption key was protected by another Windows account
  or on another computer (copied profile, password reset by an administrator). The unreadable key is set aside and
  replaced; saved passwords and API keys must be entered again.

## 0.1.2

- GeoParquet reader: reads an `https://` address by ranges, so only the needed parts of the file are downloaded
  (with *Maximum rows read*, little more than those rows); uses the proxy from the settings, and a file changed on the
  server is read again on the next run. DuckDB's `httpfs` extension ships with the installers.
- Other readers reject a web address with a clear message.

## 0.1.1

- Installers rebuilt from the cleaned-up sources: documentation, comments and the AI assistant's instructions no
  longer compare Carriole with other tools.
- Simpler website.

## 0.1.0 — first public release

- Geospatial-first visual ETL: flow editor, 104 modifiers (vector, raster, databases), table and map inspector,
  background and scheduled runs, AI assistant, English and French interface.
- Windows installer (`Carriole-Setup-0.1.0.exe`) and Linux AppImage (`Carriole-0.1.0-x86_64.AppImage`).
- Open source under the MIT license.
- Building blocks are called **modifiers**, with original names inspired by common GIS tools (`Buffer`, `Clip`,
  `JoinByLocation`, `ExtractByExpression`…); flows saved with older names are migrated when opened.
