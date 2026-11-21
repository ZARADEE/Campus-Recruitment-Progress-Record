# Contributing

Thanks for helping improve Campus Job Tracker. Bug reports, documentation fixes, and focused pull requests are welcome.

## Development setup

The desktop application targets Windows and uses Tauri 2, Rust, SQLite, Vite, and a WebView frontend.

1. Install Node.js, the stable Rust MSVC toolchain, Microsoft C++ Build Tools, the Windows SDK, and WebView2 Runtime.
2. Run `npm ci`.
3. Run `npm run desktop:dev` to work on the desktop app.
4. Run `npm run desktop:test` and `npm run desktop:build` before submitting changes.

`npm run dev` starts a browser preview with sample data. Real file access, webpage requests, and persistence require Tauri.

## Pull requests

- Keep changes focused and describe the behavior they change.
- Add or update Rust tests when changing parsing, persistence, or validation behavior.
- Check that imported text is escaped before rendering and that external URLs remain validated in Rust.
- Do not commit `data/`, SQLite files, resumes, imported job lists, screenshots containing personal records, `.toolchain/`, or build output.
- Include the commands used to verify a change and note any platform-specific limitation.

For larger interface or data-model changes, open an issue first so the scope can be discussed before implementation.
