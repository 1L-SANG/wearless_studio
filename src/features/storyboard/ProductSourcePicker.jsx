import { useId } from 'react';
import { Icon } from '../../components/ui.jsx';
import { productSourcePhotos } from '../../lib/productSourcePhotos.js';

export function ProductSourcePicker({ block, product, catalogs, onChange }) {
  const id = useId();
  const photos = productSourcePhotos(product, block.colorId, block.shot);
  return (
    <div className="insp-sec product-source-picker">
      <div className="lbl" id={`product-source-${id}`}>원본 사진</div>
      <div className="product-source-options" role="radiogroup" aria-labelledby={`product-source-${id}`}>
        {(catalogs?.productSourcePhotos || []).map((option) => {
          const photo = photos[option.value];
          const selected = block.direction === option.value;
          return (
            <label key={option.value} className={`product-source-option${selected ? ' selected' : ''}${photo ? '' : ' unavailable'}`}>
              <input type="radio" name={`product-source-${id}`} value={option.value}
                checked={selected} disabled={!photo}
                onChange={() => { if (photo) onChange({ direction: option.value }); }} />
              <span className="product-source-tile">
                {photo ? <img src={photo.src} alt="" /> : <span className="product-source-missing">사진 없음</span>}
                {selected && <span className="product-source-check"><Icon name="check" size={12} /></span>}
              </span>
              <span className="product-source-label">{option.label}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
