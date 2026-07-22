import React from 'react';
import './DemoLanding.css';

/**
 * Landing overlay for the hosted shared-demo instance (health.demo).
 * The hero is set in the loaded demo font itself — the studio behind
 * the overlay is the visual. Dismiss lands the visitor in the real
 * app; sessionStorage keeps it dismissed for the visit.
 */
function DemoLanding({ familyId, fontLoaded, onEnter }) {
  return (
    <div className="demo-landing">
      <div className="demo-landing-inner">
        <div
          className="demo-landing-specimen"
          style={{
            fontFamily: fontLoaded && familyId ? `"${familyId}", sans-serif` : 'sans-serif',
            fontVariationSettings: '"opsz" 144, "wdth" 160, "wght" 700',
          }}
        >
          avar2 studio
        </div>
        <h1>Visual authoring and preview for avar2 variable fonts.</h1>
        <p>
          Point it at a <code>.glyphs</code> or <code>.designspace</code> file
          and author the mapping from familiar axes — weight, width, optical
          size — onto the parametric axes that shape the strokes. Every preview
          is the actual compiled font.
        </p>
        <p className="demo-landing-note">
          This is a limited-time hosted demo loaded with the CrispyMini
          exemplar — one shared session, and it resets periodically. Explore
          the instances, drag the axes, open the Preview tab. For real work,
          run it locally:
        </p>
        <pre className="demo-landing-cmd">
          pipx install https://github.com/agyeiagyeiagyei/avar2-studio/releases/latest/download/avar2_studio-0.1.0.dev6-py3-none-any.whl{'\n'}
          avar2-studio MyFont.glyphs
        </pre>
        <div className="demo-landing-actions">
          <button className="demo-landing-enter" onClick={onEnter}>
            Enter the studio →
          </button>
          <a
            className="demo-landing-gh"
            href="https://github.com/agyeiagyeiagyei/avar2-studio"
            target="_blank"
            rel="noreferrer"
          >
            GitHub
          </a>
        </div>
      </div>
    </div>
  );
}

export default DemoLanding;
