/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0b0e14',
        bg2: '#111520',
        bg3: '#171c2a',
        bg4: '#1e2436',
        border: 'rgba(255,255,255,0.07)',
        border2: 'rgba(255,255,255,0.13)',
        text: '#e8ecf4',
        text2: '#8b93a8',
        text3: '#5a6075',
        accent: '#3b8bff',
        accent2: '#6ab0ff',
        glioma: '#e85d5d',
        menin: '#a855f7',
        pitu: '#22d3b0',
        normal: '#4ade80',
        warn: '#f59e0b',
      },
      fontFamily: {
        sans: ['DM Sans', 'sans-serif'],
        mono: ['DM Mono', 'monospace'],
        serif: ['Instrument Serif', 'serif'],
      },
      borderRadius: {
        rad: '10px',
        rad2: '16px',
        rad3: '24px',
      },
    },
  },
  plugins: [],
};
