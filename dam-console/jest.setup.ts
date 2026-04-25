import '@testing-library/jest-dom'

// Mock lucide-react to avoid issues in test environment
jest.mock('lucide-react', () => {
  const React = require('react');
  const lucide = jest.requireActual('lucide-react');
  const mockIcons = {};

  // Create a mock component for every icon
  Object.keys(lucide).forEach(key => {
    if (typeof lucide[key] === 'function' || (typeof lucide[key] === 'object' && lucide[key] !== null)) {
      mockIcons[key] = (props) => React.createElement('span', { ...props, 'data-testid': `icon-${key}` });
    }
  });

  return mockIcons;
});
