# Changelog

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
