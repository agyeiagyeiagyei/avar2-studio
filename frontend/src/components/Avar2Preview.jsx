import React, { useState, useEffect, useMemo } from 'react';
import './Avar2Preview.css';
import AxisControl from './AxisControl';

// Font size stays in rem internally; the slider presents it in pt
// (12pt = 1rem at the default 16px root — same convention as Sidebar).
const remToPt = (rem) => rem * 12;
const ptToRem = (pt) => pt / 12;
const roundHalf = (v) => Math.round(v * 2) / 2;
const formatPt = (rem) => {
  const pt = roundHalf(remToPt(rem));
  return Number.isInteger(pt) ? String(pt) : pt.toFixed(1);
};

function Avar2Preview({
  avar2Instances,
  avar2Axes,
  fontUrl,
  fontLoaded,
  vfFamilyId,
  builtFontFilename,
  sampleText,
  onSampleTextChange,
  fontSize,
  onFontSizeChange
}) {
  // Coordinates start empty and are populated from /api/avar2/axes metadata
  // once it arrives. Keying the state by axis tag rather than hardcoding
  // {XTRA, XOPQ, YOPQ, ...} keeps the component generic across fonts.
  const [parametricCoordinates, setParametricCoordinates] = useState({});
  const [traditionalCoordinates, setTraditionalCoordinates] = useState({});

  // Derive axis lists from /api/avar2/axes metadata. The is_parametric flag
  // is set server-side: true for axes present in the Glyphs file, false for
  // axes that only exist in the CSV (the avar2 inputs).
  const parametricAxes = useMemo(() => {
    if (!avar2Axes?.metadata) return [];
    const result = [];
    Object.entries(avar2Axes.metadata).forEach(([key, meta]) => {
      if (!meta || meta.is_parametric !== true) return;
      result.push({
        tag: key,
        name: meta.display_name || key,
        min: meta.min ?? 0,
        max: meta.max ?? 1000,
        default: meta.default ?? meta.min ?? 0,
      });
    });
    return result;
  }, [avar2Axes]);

  const traditionalAxes = useMemo(() => {
    if (!avar2Axes?.metadata) return [];
    const result = [];
    Object.entries(avar2Axes.metadata).forEach(([key, meta]) => {
      if (!meta || meta.is_parametric === true) return;
      // Traditional axes in CSS font-variation-settings use the lowercase
      // registered tag (wght/wdth/...). Fall back to the lowercased key.
      const tag = (meta.registered_tag || key).toLowerCase();
      result.push({
        tag,
        name: meta.display_name || tag,
        min: meta.min ?? 0,
        max: meta.max ?? 1000,
        default: meta.default ?? meta.min ?? 0,
      });
    });
    return result;
  }, [avar2Axes]);

  // Seed coordinate state from each axis's `default` the first time the
  // metadata for that axis arrives. Subsequent slider edits aren't overridden.
  useEffect(() => {
    if (parametricAxes.length === 0) return;
    setParametricCoordinates(prev => {
      const next = { ...prev };
      let changed = false;
      parametricAxes.forEach(axis => {
        if (next[axis.tag] === undefined) {
          next[axis.tag] = axis.default;
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [parametricAxes]);

  useEffect(() => {
    if (traditionalAxes.length === 0) return;
    setTraditionalCoordinates(prev => {
      const next = { ...prev };
      let changed = false;
      traditionalAxes.forEach(axis => {
        if (next[axis.tag] === undefined) {
          next[axis.tag] = axis.default;
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [traditionalAxes]);

  // Find closest mappings and interpolate between them for smooth transitions
  const interpolatedParametricValues = useMemo(() => {
    if (!avar2Instances || avar2Instances.length === 0) return null;

    // Collect all valid mappings with distances
    const mappingsWithDistances = [];
    
    avar2Instances.forEach(instance => {
      if (!instance.avar2_mapping || !instance.avar2_mapping.in) return;

      const mappingIn = instance.avar2_mapping.in;
      let distance = 0;
      let axisCount = 0;

      // Calculate distance for each traditional axis that we're using
      Object.keys(traditionalCoordinates).forEach(tag => {
        const currentValue = traditionalCoordinates[tag];
        const mappingValue = mappingIn[tag];
        
        if (mappingValue !== undefined) {
          // Normalize distance by axis range to make it comparable
          const axisMeta = avar2Axes?.metadata?.[tag.toUpperCase()];
          const range = axisMeta ? (axisMeta.max - axisMeta.min) : 1000;
          const normalizedDistance = Math.abs(currentValue - mappingValue) / range;
          distance += normalizedDistance;
          axisCount++;
        }
      });

      // Only consider mappings that have at least one matching axis
      if (axisCount > 0) {
        mappingsWithDistances.push({
          mapping: instance.avar2_mapping,
          distance: distance / axisCount, // Average normalized distance
          in: mappingIn
        });
      }
    });

    if (mappingsWithDistances.length === 0) return null;

    // Sort by distance
    mappingsWithDistances.sort((a, b) => a.distance - b.distance);
    
    const closest = mappingsWithDistances[0];
    
    // If we have a very close match (within 1% normalized distance), use it directly
    if (closest.distance < 0.01) {
      return closest.mapping.out;
    }

    // Otherwise, interpolate between the two closest mappings
    if (mappingsWithDistances.length >= 2) {
      const first = mappingsWithDistances[0];
      const second = mappingsWithDistances[1];
      
      // Calculate interpolation factor based on distance
      // Closer to first = more weight to first
      const totalDistance = first.distance + second.distance;
      const firstWeight = totalDistance > 0 ? second.distance / totalDistance : 1;
      const secondWeight = totalDistance > 0 ? first.distance / totalDistance : 0;
      
      // Interpolate parametric values
      const interpolated = {};
      const out1 = first.mapping.out || {};
      const out2 = second.mapping.out || {};
      
      // Get all parametric axis keys from metadata (generic across fonts).
      // Note: this whole interpolation block is slated for deletion in Phase 3
      // once build-on-save with fontc replaces JS-side approximation.
      const parametricKeys = parametricAxes.map(a => a.tag);
      parametricKeys.forEach(key => {
        const val1 = out1[key];
        const val2 = out2[key];
        if (val1 !== undefined && val2 !== undefined) {
          interpolated[key] = val1 * firstWeight + val2 * secondWeight;
        } else if (val1 !== undefined) {
          interpolated[key] = val1;
        } else if (val2 !== undefined) {
          interpolated[key] = val2;
        }
      });
      
      return interpolated;
    }
    
    // Fallback to closest mapping
    return closest.mapping.out;
  }, [avar2Instances, traditionalCoordinates, avar2Axes]);

  // Update parametric coordinates when traditional axes change (but only if parametric axes haven't been manually changed)
  const [parametricManuallyChanged, setParametricManuallyChanged] = useState(false);
  
  useEffect(() => {
    // Only auto-update if parametric axes haven't been manually changed.
    // Iterate every parametric axis in metadata rather than the old hardcoded
    // {XTRA, XOPQ, YOPQ, SPAC} list — keeps the interpolation generic until
    // Phase 3 replaces it with build-on-save.
    if (!parametricManuallyChanged && interpolatedParametricValues) {
      setParametricCoordinates(prev => {
        const next = { ...prev };
        parametricAxes.forEach(axis => {
          const value = interpolatedParametricValues[axis.tag];
          if (value !== undefined) next[axis.tag] = value;
        });
        return next;
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interpolatedParametricValues]);

  // Reset manual change flag when traditional axes change
  useEffect(() => {
    setParametricManuallyChanged(false);
  }, [traditionalCoordinates]);

  const handleTraditionalAxisChange = (axisTag, value) => {
    setTraditionalCoordinates(prev => ({
      ...prev,
      [axisTag]: value
    }));
  };

  const handleParametricAxisChange = (axisTag, value) => {
    setParametricManuallyChanged(true); // Mark as manually changed
    setParametricCoordinates(prev => ({
      ...prev,
      [axisTag]: value
    }));
  };

  // Build font variation settings string
  const fontVariationSettings = useMemo(() => {
    const settings = [];
    // Add parametric axes - CSS font-variation-settings uses lowercase tags
    Object.entries(parametricCoordinates).forEach(([tag, value]) => {
      settings.push(`"${tag.toLowerCase()}" ${value}`);
    });
    return settings.join(', ');
  }, [parametricCoordinates]);

  const handleDownload = () => {
    if (!fontUrl) return;
    // Add timestamp to force download
    const downloadUrl = `${fontUrl}${fontUrl.includes('?') ? '&' : '?'}download=1`;
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = builtFontFilename || `${vfFamilyId || 'avar2-font'}.ttf`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  // Load font when fontUrl changes. The family id comes from /api/health so
  // the tool works on any .glyphs file, not just Crispy.
  useEffect(() => {
    if (fontUrl && fontLoaded && vfFamilyId) {
      const loadFont = async () => {
        try {
          // Remove old font if it exists to force reload
          const oldFont = Array.from(document.fonts).find(f => f.family === vfFamilyId);
          if (oldFont) {
            document.fonts.delete(oldFont);
          }

          const fontFace = new FontFace(vfFamilyId, `url(${fontUrl})`);
          await fontFace.load();
          document.fonts.add(fontFace);
          await document.fonts.ready;
        } catch (err) {
          console.error('Failed to load avar2 font:', err);
        }
      };
      loadFont();
    }
  }, [fontUrl, fontLoaded, vfFamilyId]);

  if (!avar2Instances || avar2Instances.length === 0 || !avar2Axes) {
    return (
      <div className="avar2-preview-loading">
        <p>Loading avar2 data...</p>
      </div>
    );
  }

  if (!fontLoaded) {
    return (
      <div className="avar2-preview-loading">
        <p>Avar2 font not built yet.</p>
        <p>Click "Build Avar2 Font" in the header to build the font.</p>
      </div>
    );
  }

  return (
    <div className="avar2-preview">
      <div className="avar2-preview-sidebar">
        <h2>Avar2 Preview</h2>
        
        <div className="sample-text-section">
          <textarea
            value={sampleText}
            onChange={(e) => onSampleTextChange(e.target.value)}
            className="sample-text-input"
            placeholder="Enter sample text..."
            rows={3}
          />
        </div>

        <div className="font-size-control">
          <label>
            Font Size: {formatPt(fontSize)}pt
            <input
              type="range"
              min="9"
              max="144"
              step="0.5"
              value={roundHalf(remToPt(fontSize))}
              onChange={(e) => onFontSizeChange(ptToRem(parseFloat(e.target.value)))}
            />
          </label>
        </div>

        <div className="axes-section">
          <h3>Parametric Axes</h3>
          {parametricAxes.map(axis => (
            <AxisControl
              key={axis.tag}
              axis={axis}
              value={parametricCoordinates[axis.tag] || axis.default}
              onChange={(value) => handleParametricAxisChange(axis.tag, value)}
              disabled={false}
            />
          ))}
        </div>

        <div className="axes-section">
          <h3>Traditional Axes</h3>
          <p className="axis-description">Controls avar2 mappings</p>
          {traditionalAxes.map(axis => (
            <AxisControl
              key={axis.tag}
              axis={axis}
              value={traditionalCoordinates[axis.tag] || axis.default}
              onChange={(value) => handleTraditionalAxisChange(axis.tag, value)}
              disabled={false}
            />
          ))}
        </div>

        <div className="download-section">
          <button
            className="btn btn-primary btn-download"
            onClick={handleDownload}
            disabled={!fontLoaded}
          >
            Download Avar2 Font
          </button>
        </div>
      </div>

      <div className="avar2-preview-main">
        <div className="preview-row">
          <div
            className="preview-text"
            style={{
              fontFamily: vfFamilyId || 'sans-serif',
              fontSize: `${fontSize}rem`,
              fontVariationSettings: fontVariationSettings
            }}
          >
            {sampleText || 'The Quick Brown Fox Jumps Over The Lazy Dog 0123456789 &!'}
          </div>
          <div className="preview-coordinates">
            <div className="coordinate-group">
              <strong>Parametric:</strong>
              {Object.entries(parametricCoordinates).map(([tag, value]) => (
                <span key={tag}>{tag}: {value.toFixed(1)}</span>
              ))}
            </div>
            <div className="coordinate-group">
              <strong>Traditional:</strong>
              {Object.entries(traditionalCoordinates).map(([tag, value]) => (
                <span key={tag}>{tag}: {value.toFixed(1)}</span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Avar2Preview;
