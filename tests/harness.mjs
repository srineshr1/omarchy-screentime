// Loads the QML JS modules (lib/*.js) as plain objects under node. The only
// QML-specific syntax they contain is the `.pragma library` line, so stripping
// it and collecting the top-level function declarations is enough.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))

function topLevelFunctionNames(source) {
  const names = []
  const pattern = /^function\s+([A-Za-z_$][\w$]*)\s*\(/gm
  let match
  while ((match = pattern.exec(source)) !== null) names.push(match[1])
  return names
}

export function loadQmlJs(relativePath) {
  const source = readFileSync(join(here, "..", relativePath), "utf8")
    .replace(/^\s*\.pragma\s+library\s*$/m, "")
  const names = topLevelFunctionNames(source)
  const body = `${source}
    const module = {};
    ${names.map((name) => `module[${JSON.stringify(name)}] = ${name};`).join("\n")}
    return module;`
  return new Function(body)()
}
