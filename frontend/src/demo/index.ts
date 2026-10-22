/** True only in the static website demo build (`npm run build:demo`). Constant-folded away elsewhere. */
export const DEMO = import.meta.env.MODE === 'demo';
export const DEMO_REPO_URL = 'https://github.com/sochub/soc-hub';
export const DEMO_READ_ONLY = "This is a read-only demo with sample data, so changes aren't saved. Install sochub to try it for real.";
