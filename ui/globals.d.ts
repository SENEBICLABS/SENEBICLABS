// Ambient declarations so TypeScript accepts plain CSS side-effect imports
// (e.g. `import './docs.css'`). Next/webpack handles the real bundling; this only
// satisfies the type checker. `*.module.css` keeps Next's own typed declaration.
declare module '*.css'
