/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#F4F9FE',
          100: '#E7F0FF',
          200: '#D6E8F8',
          300: '#BFDBFE',
          400: '#60A5FA',
          500: '#3B82F6',
          600: '#1D4ED8',
          700: '#1E40AF',
          800: '#1E3A8A',
          900: '#102A43',
        },
        surface: 'var(--surface-bg)',
        'surface-card': 'var(--surface-card)',
        'dark-surface': '#0B1526',
        'dark-card': '#122033',
        'dark-card-elevated': '#1A2C44',
        'dark-border': 'rgba(255,255,255,0.1)',
        'dark-text-primary': '#F5F5F7',
        'dark-text-secondary': '#98989D',
        slate: {
          50:  '#f8fafc',
          100: '#f1f5f9',
          200: '#e2e8f0',
          300: '#cbd5e1',
          400: '#94a3b8',
          500: '#64748b',
          600: '#475569',
          700: '#334155',
          800: '#1e293b',
          900: '#0f172a',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        'display': ['3.5rem', { lineHeight: '1.1', letterSpacing: '-0.02em' }],
        'hero': ['4rem', { lineHeight: '1.05', letterSpacing: '-0.025em' }],
      },
      letterSpacing: {
        'tighter': '-0.03em',
        'tight': '-0.02em',
      },
      boxShadow: {
        'brand-sm': '0 1px 3px rgba(29,78,216,0.08)',
        'brand':    '0 4px 16px rgba(29,78,216,0.12)',
        'brand-lg': '0 8px 32px rgba(29,78,216,0.16)',
        'brand-xl': '0 16px 48px rgba(29,78,216,0.18)',
        'dark-brand': '0 4px 16px rgba(0,0,0,0.3)',
        'dark-brand-lg': '0 8px 32px rgba(0,0,0,0.4)',
      },
      backgroundImage: {
        'gradient-brand': 'linear-gradient(135deg, #1D4ED8 0%, #1D4ED8 100%)',
      },
      animation: {
        'shimmer': 'shimmer 2s infinite',
        'fade-up': 'fadeInUp 0.4s ease-out',
        'spin-slow': 'spin 3s linear infinite',
        'smooth-shimmer': 'smoothShimmer 1.5s ease-in-out infinite',
      },
      keyframes: {
        shimmer: {
          '0%':   { backgroundPosition: '-200% center' },
          '100%': { backgroundPosition: '200% center' },
        },
        fadeInUp: {
          '0%':   { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        smoothShimmer: {
          '0%':   { opacity: '0.4' },
          '50%':  { opacity: '0.7' },
          '100%': { opacity: '0.4' },
        },
      },
    },
  },
  plugins: [],
}
