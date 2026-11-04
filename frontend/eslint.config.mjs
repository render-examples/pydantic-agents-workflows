import coreWebVitals from 'eslint-config-next/core-web-vitals'

const eslintConfig = [
  ...coreWebVitals,
  { ignores: ['.next/**', 'out/**', 'node_modules/**', 'next-env.d.ts'] },
]

export default eslintConfig
