import { rm } from "node:fs/promises";
import { join } from "node:path";

export default async function globalTeardown(): Promise<void> {
  await rm(join(process.cwd(), "work", "browser-tests"), {
    recursive: true,
    force: true,
  });
}
