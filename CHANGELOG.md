# Changelog

## Unreleased

- DuckDB 2.0.0 (LTS). Flows give the same results as with 1.5: an empty (`NULL`) attribute keeps a type and can
  be written to GeoPackage, `//` and `%` by zero give NULL, `~` matches the whole value. Lambdas are written
  `lambda x : x + 1`; the old `x -> x + 1` form is no longer accepted.
- GeoParquet reader on the web: with *Maximum rows read*, DuckDB 2.0's read-ahead is turned off, so only the needed
  parts of the file are downloaded.

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
