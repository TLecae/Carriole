// Après l'assemblage : icône et informations de version dans l'exécutable (sans signature de code ni winCodeSign).
const path = require("node:path");

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "win32") return;
  const { rcedit } = await import("rcedit"); // module ESM
  const info = context.packager.appInfo;
  const exe = path.join(context.appOutDir, `${info.productFilename}.exe`);
  await rcedit(exe, {
    icon: path.join(context.packager.projectDir, "build", "icon.ico"),
    "file-version": info.version,
    "product-version": info.version,
    "version-string": {
      ProductName: info.productName,
      FileDescription: info.productName,
      CompanyName: info.companyName || "Thomas Lecae",
      LegalCopyright: info.copyright,
      OriginalFilename: `${info.productFilename}.exe`,
    },
  });
};
