import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        'dam-bg': '#0A0A0A',
        'dam-surface': '#141414',
        'dam-surface-2': '#1C1C1C',
        'dam-surface-3': '#242424',
        'dam-border': '#2A2A2A',
        'dam-text': '#F0F0F0',
        'dam-muted': '#6B6B6B',
        'dam-blue': '#3B82F6',
        'dam-blue-bright': '#60A5FA',
        'dam-blue-dim': '#0F172A',
        'dam-green': '#22C55E',
        'dam-orange': '#F97316',
        'dam-red': '#EF4444',
      },
      animation: {
        'pulse-blue': 'pulse-blue 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'pulse-red': 'pulse-red 1s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
      keyframes: {
        'pulse-blue': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.5' },
        },
        'pulse-red': {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.7', transform: 'scale(1.02)' },
        },
      },
    },
  },
  plugins: [],
}

export default config
