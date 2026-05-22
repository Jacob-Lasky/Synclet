/// <reference types="vite/client" />

// Vue Single File Component module shim. Without this, `import X from "./X.vue"`
// types `X` as `any`, which silently defeats every downstream type-aware lint
// rule (no-unsafe-argument, no-unsafe-call, etc.).
declare module "*.vue" {
    import type { DefineComponent } from "vue"
    const component: DefineComponent<
        Record<string, never>,
        Record<string, never>,
        unknown
    >
    export default component
}
