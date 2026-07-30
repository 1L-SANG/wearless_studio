export function resolveSelectedModelId({
  selectedModelId,
  targetGenders,
  models,
  modelsLoading,
  aiModels,
}) {
  if (modelsLoading) return selectedModelId;

  const licensable = models.filter((model) => model.hasActiveLicense);
  const valid = aiModels.some((model) => model.id === selectedModelId)
    || licensable.some((model) => model.id === selectedModelId);
  if (valid) return selectedModelId;

  const targetGender = targetGenders?.[0];
  const pool = targetGender
    ? aiModels.filter((model) => model.gender === targetGender)
    : aiModels;
  return (pool[0] || aiModels[0])?.id;
}
