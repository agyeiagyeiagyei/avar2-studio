import React from 'react';
import './Header.css';

function Header({ onBuildFont, building, fontLoaded, familyName }) {
  return (
    <header className="header">
      {familyName && <h1>{familyName}</h1>}
      <div className="header-actions">
        <button
          onClick={onBuildFont}
          disabled={building}
          className="btn btn-primary"
        >
          {building ? 'Building...' : fontLoaded ? 'Rebuild Font' : 'Build Font'}
        </button>
      </div>
    </header>
  );
}

export default Header;
