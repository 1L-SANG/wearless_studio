import s from './FormInput.module.css';

export function FormInput({
  label,
  required = false,
  unit,
  optional = false,
  hint,
  error,
  messageId,
  className = '',
  inputClassName = '',
  ...inputProps
}) {
  const hasMessage = Boolean(error || hint);
  return (
    <label className={`${s.field}${className ? ` ${className}` : ''}`}>
      <span className={s.label}>{label}{required && <span className={s.required}>*</span>}{optional && <span className={s.optional}>선택</span>}</span>
      <span className={`${s.control}${unit ? ` ${s.withUnit}` : ''}`} data-invalid={Boolean(error) || undefined}>
        <input
          {...inputProps}
          className={`${s.input}${inputClassName ? ` ${inputClassName}` : ''}`}
          required={required || inputProps.required || undefined}
          aria-label={inputProps['aria-label'] || label}
          aria-invalid={Boolean(error) || inputProps['aria-invalid'] || undefined}
          aria-describedby={(hasMessage && messageId) || inputProps['aria-describedby']}
        />
        {unit && <span className={s.unit}>{unit}</span>}
      </span>
      {hasMessage && <span id={messageId} className={`${s.message}${error ? ` ${s.error}` : ''}`} role={error ? 'alert' : undefined}>{error || hint}</span>}
    </label>
  );
}

export default FormInput;
