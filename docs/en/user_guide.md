# User guide

Who it is for: people who build data-processing flows (GIS, spreadsheets, databases). No programming needed. To **write a new
modifier in Python**, see [ecrire_un_modificateur.md](../ecrire_un_modificateur.md) (French).

The interface is in French or English: choose at installation, change it any time in **Settings**
(the language is a user setting). The modifier catalog in English is
[catalogue.md](catalogue.md).

## 1. The principle

A **flow** is a diagram: **nodes** (each does one operation) linked by **connections**. The data (*features*: a table
row, possibly with a geometry) travels from left to right. The flow runs in the background on your computer; to close the
window without interrupting a run, turn on *Keep Carriole running in the background* (*Settings → General*), otherwise
closing offers to let the running runs finish.

Each node has **ports**: inputs on the left, outputs on the right. A node that knows it can fail on some features has a
**Rejected** output: the failing features go there with the reason (`_rejection_reason`) instead of stopping the flow. A
**configuration** error (attribute not found, missing file) does stop the flow, with a message saying which node and why.

## 2. Building a flow

A **flow is a `.json` file**, stored wherever you like (usually on the team share). When the application
opens, the **home page** lists your **recent** flows (description, location, node count, state of the last run): a click
opens a flow, **Open a flow…** picks another file, **+ New flow** starts one, the search box filters the list. The cross
**removes** a flow from the list and never deletes its file. In the editor, **⌂ Home** goes back without losing
anything: **Resume** brings the editor back as you left it.

1. **Add a node**: double-click in the palette (on the left: categories are folded, a click unfolds one; the search
   at the top unfolds them all), or, on the canvas, **type a few letters**: a quick-add field opens; Enter adds the first result and **links it to the selected node**.
2. **Link** two nodes: drag from an output to an input. The attributes available upstream are offered in the forms; a
   warning appears (*Warnings* tab) when an attribute does not exist upstream or an input is not connected.
3. **Set up** a node: click it, the form is on the right. Under each field, a sentence says **what it is for and what an
   empty field means**; an empty field shows a greyed **example**, and hovering a label shows the help. The *Help* at
   the bottom of the form details the modifier. *Expression* fields accept SQL (DuckDB): `"pop" >= 1000`,
   `upper("name")`, `ST_Area(geometry)`. Attribute names are written between double quotes.
4. **Save**: `Ctrl+S`. A new flow is called "New flow" and marked *not saved*; the first save asks where to put the
   file, and **the flow takes the name of that file** (one name only: nothing else to type). The next saves rewrite
   the same file (a dot shows unsaved changes). **Save as…** (`Ctrl+Shift+S`) makes a copy elsewhere, or under another
   name: that is how a flow is renamed. If a colleague changed the file since you opened it, saving is **refused**
   (nothing is overwritten): *Save as…* keeps your work.

Selection: click a node, or drag a frame on the canvas background; `Ctrl`+click (or `Shift`+click) adds a node to the
selection or removes it. Dragging one of the selected nodes moves the whole group. Clicking a node shows its data in
the *Data* tab (or says it has none yet).

**Node bar**: above the selected node, three icons (tooltip on hover): **▶|** run the flow up to this node, **⏻**
disable or enable it, **▦** see its data.

Shortcuts: `Ctrl+Z` / `Ctrl+Y` undo / redo, `Ctrl+C` / `Ctrl+V` copy / paste nodes, `Alt+D` enable / disable the
selected nodes (a disabled node is skipped with everything downstream). The **Arrange** menu: notes, bookmarks
(collapsible groups), alignments.

### Published parameters
In the *Flow* panel (click the canvas background): values entered **at launch** (a département, a vintage, a file). In
any node parameter: `${NAME}`; environment variable of the machine: `${env:NAME}`.

## 3. Running and following

- **Run** executes the flow; **Up to node** (*Run* menu or node bar) stops at the selected node, reusing the cache
  of everything unchanged, **database reads included**; nodes of the other branches stay
  inspectable.
- **Caching** (*Cache* button next to *Run*, or *Run* menu): one setting for the whole flow, remembered
  between sessions:
  - **on** (default): the data of every node is kept, for the inspector; on the next run, **only the changed nodes
    (and their downstream) run**, the others are reused from the previous run (*cache* state). Writers, database,
    web API and STAC readers, SQL and launched flows **always run again** with *Run* (their source can change
    without the flow changing), with all their downstream; *Up to node* reuses the reads (not the writes). *Run
    everything again* (*Run* menu) recomputes everything;
  - **off**: everything is recomputed and nothing is kept: faster and less disk, but no inspector (except for the
    last nodes of the flow). An unconnected output (`Failed` of a filter…) is only counted.
- **Disk space** (desktop application): only the data of the **last run of each open flow** is kept, plus that of the
  running runs. Closing the flow (opening another flow, *New flow*) or the window deletes it within seconds; after a
  crash, at the next start. The log and counts of runs stay available in *Runs*, without their data.
- **Counts** are shown **next to each node port** (inputs and outputs: `output`, `Passed`, `Failed`, `Rejected`…,
  even for an unconnected port), live during the run, abbreviated (`138 k`, `1.2 M`): the exact number is in the
  tooltip. The state is on each node, the **log** at the
  bottom (a click on a line selects the node). Cancel: *Stop* button.
- **Runs**: list of all runs (running, finished, failed, interrupted). You can reopen them, see the log and the data,
  **rerun** (same flow, as it was executed) or **resume** after a stop: finished nodes are not recomputed. An end-of-run
  e-mail is possible if the server offers it.

## 4. Inspecting the data

**Data** tab: click a node or a connection (the tab shows up). Table (sort, per-column filter, search, free `WHERE` clause) and **map**
synchronized: selecting a row highlights it on the map and vice versa. Everything is computed server-side, even with
millions of features. *Export*: CSV, GeoPackage or Parquet of the filtered view.

Filters and attribute operations (*Extract by expression*, *Multi-output filter*, *Create attributes*, *Manage
attributes*, *Convert attribute types*, *Keep attributes*, *Remove attributes*, *Rename attributes*, *Rename in bulk*)
do not write their result during the run: it is computed when a downstream node reads it, or when it is opened here. The first opening shows
"Preparing data…" while it is computed (a few seconds for a common file; several minutes for millions of rows with
hundreds of columns read from a network drive), then the result is kept with the run. Sort, statistics and attribute
join do the same for an output that no node reads, such as the features with their group's statistics when only the
summary is used. If you switch to another port while it is being prepared, the preparation stops.

During a run, the selected node stays displayed, with "… is being computed" until its data is there.

The table shows **every feature** of the port: rows arrive as you scroll, and filters, sort, search, statistics
and export cover the whole port. The feature count is shown once ("11 / 20 features" when a filter keeps 11).

**Columns**: drag the right edge of a header to widen the column, double-click to fit it to its content. **Copy**:
right-click a cell (*Copy value*, *Copy row*, *Copy column name*) or a header; or click a cell then `Ctrl+C`. Text can
also be selected with the mouse.

The first opening of a port prepares its map **in the background**: the table can be browsed right away, the map
shows "Loading the map…" (about 30 seconds for 1.6 million parcels, only once). If the geometries are too large
for memory, the map says so in its place (the table can still be browsed). The map is made of vector tiles,
generalized by zoom: outlines are simplified to half a pixel, a feature smaller than a pixel is a one-pixel dot;
detail comes back when zooming in. A huge polygon (output of a *Dissolve* or a merged *Buffer*, thousands of holes)
is cut into pieces for the map: only its outline is drawn, the cuts do not show. The map draws **at most 50,000
features per area** (4 × 4 tiles): beyond that,
**the largest** (the same in every tile of the area), the smallest appear when zooming in; for points, a sample. A tile always has the same content whatever the view: panning or zooming
reloads nothing already received, and small-scale tiles are computed in advance (about 35 s for 1.6 million parcels,
after the map is prepared), for the last opened port: switching to another port does not make it wait for the
tiles of the previous one. Changing the filter or the colour keeps the old image until the new one arrives. The
table always shows every feature. Selecting a huge feature highlights a simplified outline.

**Colour**: colours the map by an attribute, computed on the filtered rows. A numeric attribute gives five equal-count
classes, from light to dark; another attribute, one colour per frequent value (the ten most frequent, the others in
grey). **Overlay** adds other ports of the run to the map. As soon as there is a colour or several layers, the
**Legend** button, under the zoom buttons, explains them. Clicking a feature on the map selects it and scrolls the
table to its row ("Finding the feature in the table…" while it is being found).

**Panel size**: drag the bar above the tabs (or `↑` / `↓` when focused); the height is remembered. **⤢ Full page** opens
the panel over the whole window (Esc to go back to the editor).

Map **basemap**: **Plan** (OpenStreetMap) and **Satellite** (Esri) buttons; the ⚙ adds IGN aerial photography, no
basemap (offline) or a custom tile address. Tiles are loaded from the internet; see the
[installation](../installation.md#fonds-de-plan-de-la-carte) if the map stays grey.

## 5. Files and databases

| Need | Modifier |
| --- | --- |
| Read / write a file | one reader and one writer **per format** (*Reading* and *Writing* categories): *CSV reader*, *GeoPackage reader*, *Shapefile reader*, *Excel reader*, *GeoJSON reader*, *(Geo)Parquet reader* (Parquet with or without geometry), *FlatGeobuf reader*, *MapInfo reader*, and the matching *… writer* |
| Read / write a database | *PostgreSQL / PostGIS*, *Oracle* reader / writer; *SQLite / SpatiaLite* reader / writer (file) |
| Run a SQL statement | *SQL (creator)*; *SQL (per feature)* with `{{attribute}}` |

- Files are **those of your computer and of the shares you can access** (`Z:`, network paths). The **Browse…** button
  next to a file field opens the Windows file picker. You can also type the path by hand. A pattern
  (`D:/communes/*.gpkg`) reads several files.
- Each reader or writer has **only the settings of its format** (delimiter and X/Y columns for CSV, layer for
  GeoPackage, sheet for Excel…), and **Browse…** shows only the files of that format. Flows saved with the former
  *File reader* are converted when opened.
- **Maximum rows read** (every reader, files and databases): to try a flow on a sample, only the first N features
  are read (0 = all). The node then shows a “≤ N” badge; hovering it shows a reminder.
- **(Geo)Parquet read in place**: a single local file, without *Add the file name* or *Maximum rows read*, is not
  copied into the cache: the node's result is the file itself, read in a fraction of a second whatever its size. If
  the file is changed or moved after the run, run the flow again to view it.
- **(Geo)Parquet on the web**: *(Geo)Parquet reader* accepts an `https://…/file.parquet` address. The file is read by
  ranges: only the useful parts are downloaded (with *Maximum rows read*, barely more than those rows). The proxy from
  *Settings* is used; a file changed on the server (ETag, date) is read again on the next run.
- **Writing to the share**: a writer can target the team share. Two flows modifying the same GeoPackage at the
  same time overwrite each other: avoid it. For a scheduled task, prefer a network path (`//server/share/…`) to a
  drive letter, which the task's session does not always see.
- **Connections** (*Tools* menu): choose the database type (PostgreSQL / PostGIS, Oracle), then enter host,
  database (Oracle: service), user, password once; flows only contain the *name*. The password is encrypted and never
  shown again. The readers and writers of each
  database only offer connections of their type; *SQL (creator)* and *SQL (per feature)* accept every database (the
  SQL is that of the database). Nothing to install on the computer.
- Format details (Shapefile 10 characters, Excel, partitions…): [formats.md](../formats.md) (French).

## 6. The catalog

All the modifiers, with their parameters: [catalogue.md](catalogue.md). Some landmarks: **Filters** (Test, spatial
filter, duplicates, geometry type), **Attributes** (create, manage, rename, keep, remove, lookup table, empty values,
split, explode), **Calculations** (statistics, aggregate, counter, sort), **Joins** (attribute, merge, spatial relator,
point in polygon, SQL), **Geometry** (reproject, buffer, clip, dissolve, validate, **repair**, simplify, coordinates,
area…).

**Invalid geometries** (self-intersection, unclosed ring…): *Validate geometries* sorts the valid ones from the invalid
ones with the reason; *Repair geometries* makes them valid (the others pass through untouched) and sends to *Rejected*
those that give nothing once repaired. Place it before a clip, a spatial join or a PostGIS write that refuses invalid
geometries.

## 7. Components: reuse a piece of flow

1. Build a flow with **Component input** and **Component output** nodes (one per port, named).
2. *Flow* panel → check **This flow is a reusable component**; choose identifier, label and category. The flow's
   *published parameters* become the component's parameters.
3. Save the file **in a components folder** (`component_dirs` setting, usually a folder on the share: the whole team
   shares the same components). In other flows, the component appears **in the palette** like a modifier; the ↗
   button on the node opens its definition.

A component can be run alone to test it (give a sample file on the input). A run keeps the component version it used:
rerunning it later replays the same thing. [ADR 0014](../decisions/0014-composants.md).

### Component versions
As long as nothing is published, flows use the component as saved: any change affects them. To evolve a component
without surprises:

1. Save the component, then, in the flow panel, **Publish a version** (with a note: what changes). A version is a
   snapshot that never moves.
2. In a flow that uses it, select the component node: **Component version**. A node added from the palette is pinned
   to the latest version; choosing "Latest published" makes it follow new versions. A message tells when a newer version
   exists.

The draft (saved but unpublished flow) does not affect flows once a version exists.
[ADR 0017](../decisions/0017-versions-de-composants.md).

## 8. Scheduling

**Schedules** button: choose a *saved* flow, a cron expression (templates are offered; the next occurrences are shown),
the published parameters, an optional e-mail. The workers run the flow on time, with no browser open. *Run now* tests the
schedule. A skipped occurrence (previous run still running, workers stopped) is shown in the schedule's row.
[ADR 0015](../decisions/0015-planification.md).

Cron format: `minute hour day-of-month month day-of-week`. Examples: `0 2 * * *` every night at 2 am, `30 6 * * 1-5`
weekdays at 6:30 am, `0 3 1 * *` the 1st of the month at 3 am.

## 9. When something goes wrong

| Symptom | Lead |
| --- | --- |
| "unknown connection" | create it in *Connections* (same name) |
| "password … unreadable" | this machine's encryption key changed (other account, other machine): enter the password again |
| Run "interrupted" | a worker stopped: *Resume* from the runs list |
| "The engine is not responding" banner | the application restarts it by itself (a few seconds); do not close the window: a save requested meanwhile is done when it is back. Details in `logs\server.log` (*Settings* → logs folder) |
| Red node with "configuration: …" | the message says what to fix; the log gives the detail |
| Data purged | the data of a closed flow is deleted (on a server: after a few days): rerun the flow |
| "The flow was modified by someone else" | the file changed on disk: *Save as…* to keep your version |

## 10. Variables, folders and batches

**Variables**: in any node parameter, `${…}` is replaced by a value. The `${…}` picker next to the fields lists what is
available, and the resolved value is shown under the field.

| Written | Value |
| --- | --- |
| `${NAME}` | a published parameter, or a **flow variable** (*Flow* panel → *Flow variables*: constants, or values that cite others: `${ROOT}/output`) |
| `${sys:date}`, `${sys:flow_dir}`, `${sys:run_id}`… | the context: date, time, user, folder and name of the flow, run number, temp folder |
| `${global:NAME}` | a variable shared by all your flows (*Settings → Global variables*) |
| `${env:NAME}` | an environment variable |
| `${var:NAME}` | a value **taken from the data** by a *Set variables* node placed upstream |

**Folders of files**: *List files* gives one feature per file (path, name, size, date); *Read the listed files* reads them
all and merges their columns; *Write one file per group* stores the results in one file per value of an attribute
(`D:\outputs\{department}\communes.gpkg`). *Run a flow* runs another flow for each file.

**Batches**: the **Batch…** button runs the flow once per file of a pattern (`D:/communes/*.shp`) or per value of a
list: choose the published parameter that receives each value. The runs are grouped in the runs list. On the command line:
`etl run flow.json --each "D:\communes\*.shp" --as SRC --parallel 4` (see the [installation guide](../installation.md#ligne-de-commande)).

## 11. Rasters (DEMs, satellite imagery)

A raster is a georeferenced image (GeoTIFF, COG, ASC, JP2, VRT…). In a flow, **a raster port holds one raster per row**:
the row describes the file (size, bands, coordinate system) and carries its extent; pixels stay in the files and are only
read window by window, so memory does not depend on the size of the image.

- **Read**: `RasterReader` (file, `*.tif` pattern, list, `https://` address). **Search**: `STACSearch` (Sentinel-2,
  Landsat… on a STAC catalog: extent, dates, cloud cover, wanted assets).
- **Compute**: `RasterCalculator` (NDVI: `(nir - red) / (nir + red)`, band names are the variables),
  `ReclassifyRaster`, `TerrainAnalysis` (slope, aspect, hillshade), `Contour`.
- **Geometry**: `WarpRaster`, `ResampleRaster`, `ClipRasterByMask`, `MergeRasters` (tiles or a composite over time),
  `StackBands`.
- **To features**: `ZonalStatistics` (mean, sum… per municipality), `SampleRasterValues`, `Polygonize`,
  `RasterStatistics`; **features to a raster**: `Rasterize`. **Write**: `RasterWriter`.
- **Inspect**: select a raster node in the Data tab. The raster extents are drawn on the map together with the image
  (palette or red-green-blue composite, adjustable range); clicking the map gives the value of each band at that pixel.
  Selecting another row of the table switches raster.
- Remote images (STAC, COG) go through the workstation proxy and are only read by ranges.

## 12. The AI assistant

The **✨ Assistant** button opens a panel where you describe what you want in French or English: build a flow, explain an
error, understand a node, write a modifier. The assistant **reads** (catalog, open flow, files, runs) but **never changes
anything by itself**: its changes arrive as proposals (list of changes, analysis warnings) that you **apply with one click**
or dismiss. An applied proposal can be undone with Ctrl+Z.

- **Set up** (⚙): add Claude (Anthropic), OpenAI, OpenRouter or a local model (Ollama); enter the API key (encrypted on
  this computer, never displayed), load the model list from the provider and pick one.
- **`@` references**: type `@` to attach a node (`@Reader_1`), a run output (`@Reader_1.output`) or a file (`Choose a file…`,
  or drop it on the panel in the desktop application). The assistant receives a summary.
- **Privacy**: the 🔒 banner tells what is sent. By default a remote provider only gets **schemas** (field names and
  types, counts, coordinate system), never values; a local model may get a few sample rows (adjustable). The text you
  write, the open flow (parameters and paths included) and the log messages are always sent: do not put passwords in
  a flow (use connections or `${env:NAME}`).
- **Writing a modifier**: the assistant proposes the code and its test; after review, “Test and install” runs the
  test in an isolated process and only installs the plugin if it passes. The plugin then runs with your rights.
- On the command line: `etl ai "add a filter on population" --flow flow.json --out flow2.json`.
