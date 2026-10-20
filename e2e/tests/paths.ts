// Dossiers de données des scénarios (créés par playwright.config.ts), en barres obliques : valables sous Windows.
const slash = (p: string) => p.replace(/\\/g, "/");
export const IN = slash(process.env.E2E_IN!);
export const OUT = slash(process.env.E2E_OUT!);
