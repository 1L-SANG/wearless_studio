-- AI 재태깅 제안 검수 후 수동 실행. 이 파일을 생성한 스크립트는 DB에 접속하지 않습니다.
BEGIN;

UPDATE matching_items SET style_tags = '["basic","casual","preppy","classic"]'::jsonb WHERE id = 'match_men_bottom_01' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","minimal","trendy"]'::jsonb WHERE id = 'match_men_bottom_02' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","daily","cozy","athleisure"]'::jsonb WHERE id = 'match_men_bottom_03' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["workwear","vintage","casual","street"]'::jsonb WHERE id = 'match_men_bottom_04' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","vintage","street"]'::jsonb WHERE id = 'match_men_bottom_05' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","vintage","street","y2k"]'::jsonb WHERE id = 'match_men_bottom_06' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","casual","preppy","classic"]'::jsonb WHERE id = 'match_men_bottom_07' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","preppy","minimal"]'::jsonb WHERE id = 'match_men_bottom_08' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["cozy","casual","daily"]'::jsonb WHERE id = 'match_men_bottom_09' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["minimal","sophisticated","modern"]'::jsonb WHERE id = 'match_men_bottom_10' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","street","vintage","y2k"]'::jsonb WHERE id = 'match_men_bottom_11' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","minimal","formal","sophisticated"]'::jsonb WHERE id = 'match_men_bottom_12' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","workwear","vintage","street"]'::jsonb WHERE id = 'match_men_bottom_13' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["street","vintage","casual"]'::jsonb WHERE id = 'match_men_bottom_14' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","sporty","athleisure","daily"]'::jsonb WHERE id = 'match_men_bottom_15' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","daily","vintage"]'::jsonb WHERE id = 'match_women_bottom_02' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","daily","vintage"]'::jsonb WHERE id = 'match_women_bottom_03' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","daily","vintage"]'::jsonb WHERE id = 'match_women_bottom_04' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["minimal","sophisticated","modern","classic"]'::jsonb WHERE id = 'match_women_bottom_05' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","street","vintage"]'::jsonb WHERE id = 'match_women_bottom_06' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["minimal","casual","cozy"]'::jsonb WHERE id = 'match_women_bottom_07' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","athleisure","cozy","daily"]'::jsonb WHERE id = 'match_women_bottom_08' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["minimal","sophisticated","modern","casual"]'::jsonb WHERE id = 'match_women_bottom_09' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","street","vintage"]'::jsonb WHERE id = 'match_women_bottom_10' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["minimal","casual","trendy"]'::jsonb WHERE id = 'match_women_bottom_11' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["minimal","chic","sophisticated","formal"]'::jsonb WHERE id = 'match_women_bottom_12' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","cozy","vintage"]'::jsonb WHERE id = 'match_women_bottom_13' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["cozy","casual","minimal"]'::jsonb WHERE id = 'match_women_bottom_14' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["preppy","lovely","casual"]'::jsonb WHERE id = 'match_women_bottom_15' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["chic","modern","sophisticated","classic"]'::jsonb WHERE id = 'match_women_bottom_16' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_01' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_02' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","casual","minimal"]'::jsonb WHERE id = 'match_men_top_03' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_04' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_05' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["classic","minimal","casual","preppy"]'::jsonb WHERE id = 'match_men_top_06' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","preppy","retro"]'::jsonb WHERE id = 'match_men_top_07' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","cozy","daily"]'::jsonb WHERE id = 'match_men_top_08' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","retro","daily"]'::jsonb WHERE id = 'match_men_top_10' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["minimal","casual","classic","preppy"]'::jsonb WHERE id = 'match_men_top_11' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","minimal","casual"]'::jsonb WHERE id = 'match_men_top_12' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","cozy","daily"]'::jsonb WHERE id = 'match_men_top_13' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_14' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_15' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_16' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","cozy","vintage"]'::jsonb WHERE id = 'match_women_top_01' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","daily","classic","preppy"]'::jsonb WHERE id = 'match_women_top_02' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","casual","minimal"]'::jsonb WHERE id = 'match_women_top_03' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","minimal","casual","preppy"]'::jsonb WHERE id = 'match_women_top_04' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["casual","cozy","vintage"]'::jsonb WHERE id = 'match_women_top_05' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","casual","cozy"]'::jsonb WHERE id = 'match_women_top_06' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","casual","minimal"]'::jsonb WHERE id = 'match_women_top_07' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_women_top_08' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","minimal","cozy"]'::jsonb WHERE id = 'match_women_top_09' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["minimal","classic","sophisticated","casual"]'::jsonb WHERE id = 'match_women_top_10' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_women_top_11' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["cozy","feminine","daily","minimal"]'::jsonb WHERE id = 'match_women_top_13' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["minimal","chic","modern","basic"]'::jsonb WHERE id = 'match_women_top_14' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["chic","feminine","modern"]'::jsonb WHERE id = 'match_women_top_15' AND owner_user_id IS NULL AND project_id IS NULL;

UPDATE matching_items SET style_tags = '["basic","minimal","daily","casual"]'::jsonb WHERE id = 'match_women_top_16' AND owner_user_id IS NULL AND project_id IS NULL;

-- 적용 검증: 기대 태그와 완전 일치하는 큐레이션 행이 60건이 아니면 예외로 롤백된다.
DO $$
DECLARE matched integer;
BEGIN
  SELECT COUNT(*) INTO matched
  FROM matching_items mi
  JOIN (VALUES
    ('match_men_bottom_01', '["basic","casual","preppy","classic"]'::jsonb),
    ('match_men_bottom_02', '["casual","minimal","trendy"]'::jsonb),
    ('match_men_bottom_03', '["casual","daily","cozy","athleisure"]'::jsonb),
    ('match_men_bottom_04', '["workwear","vintage","casual","street"]'::jsonb),
    ('match_men_bottom_05', '["casual","vintage","street"]'::jsonb),
    ('match_men_bottom_06', '["casual","vintage","street","y2k"]'::jsonb),
    ('match_men_bottom_07', '["basic","casual","preppy","classic"]'::jsonb),
    ('match_men_bottom_08', '["casual","preppy","minimal"]'::jsonb),
    ('match_men_bottom_09', '["cozy","casual","daily"]'::jsonb),
    ('match_men_bottom_10', '["minimal","sophisticated","modern"]'::jsonb),
    ('match_men_bottom_11', '["casual","street","vintage","y2k"]'::jsonb),
    ('match_men_bottom_12', '["basic","minimal","formal","sophisticated"]'::jsonb),
    ('match_men_bottom_13', '["casual","workwear","vintage","street"]'::jsonb),
    ('match_men_bottom_14', '["street","vintage","casual"]'::jsonb),
    ('match_men_bottom_15', '["casual","sporty","athleisure","daily"]'::jsonb),
    ('match_women_bottom_02', '["casual","daily","vintage"]'::jsonb),
    ('match_women_bottom_03', '["casual","daily","vintage"]'::jsonb),
    ('match_women_bottom_04', '["casual","daily","vintage"]'::jsonb),
    ('match_women_bottom_05', '["minimal","sophisticated","modern","classic"]'::jsonb),
    ('match_women_bottom_06', '["casual","street","vintage"]'::jsonb),
    ('match_women_bottom_07', '["minimal","casual","cozy"]'::jsonb),
    ('match_women_bottom_08', '["casual","athleisure","cozy","daily"]'::jsonb),
    ('match_women_bottom_09', '["minimal","sophisticated","modern","casual"]'::jsonb),
    ('match_women_bottom_10', '["casual","street","vintage"]'::jsonb),
    ('match_women_bottom_11', '["minimal","casual","trendy"]'::jsonb),
    ('match_women_bottom_12', '["minimal","chic","sophisticated","formal"]'::jsonb),
    ('match_women_bottom_13', '["casual","cozy","vintage"]'::jsonb),
    ('match_women_bottom_14', '["cozy","casual","minimal"]'::jsonb),
    ('match_women_bottom_15', '["preppy","lovely","casual"]'::jsonb),
    ('match_women_bottom_16', '["chic","modern","sophisticated","classic"]'::jsonb),
    ('match_men_top_01', '["basic","daily","minimal","casual"]'::jsonb),
    ('match_men_top_02', '["basic","daily","minimal","casual"]'::jsonb),
    ('match_men_top_03', '["basic","daily","casual","minimal"]'::jsonb),
    ('match_men_top_04', '["basic","daily","minimal","casual"]'::jsonb),
    ('match_men_top_05', '["basic","daily","minimal","casual"]'::jsonb),
    ('match_men_top_06', '["classic","minimal","casual","preppy"]'::jsonb),
    ('match_men_top_07', '["casual","preppy","retro"]'::jsonb),
    ('match_men_top_08', '["casual","cozy","daily"]'::jsonb),
    ('match_men_top_10', '["casual","retro","daily"]'::jsonb),
    ('match_men_top_11', '["minimal","casual","classic","preppy"]'::jsonb),
    ('match_men_top_12', '["basic","minimal","casual"]'::jsonb),
    ('match_men_top_13', '["casual","cozy","daily"]'::jsonb),
    ('match_men_top_14', '["basic","daily","minimal","casual"]'::jsonb),
    ('match_men_top_15', '["basic","daily","minimal","casual"]'::jsonb),
    ('match_men_top_16', '["basic","daily","minimal","casual"]'::jsonb),
    ('match_women_top_01', '["casual","cozy","vintage"]'::jsonb),
    ('match_women_top_02', '["casual","daily","classic","preppy"]'::jsonb),
    ('match_women_top_03', '["basic","daily","casual","minimal"]'::jsonb),
    ('match_women_top_04', '["basic","minimal","casual","preppy"]'::jsonb),
    ('match_women_top_05', '["casual","cozy","vintage"]'::jsonb),
    ('match_women_top_06', '["basic","daily","casual","cozy"]'::jsonb),
    ('match_women_top_07', '["basic","daily","casual","minimal"]'::jsonb),
    ('match_women_top_08', '["basic","daily","minimal","casual"]'::jsonb),
    ('match_women_top_09', '["basic","daily","minimal","cozy"]'::jsonb),
    ('match_women_top_10', '["minimal","classic","sophisticated","casual"]'::jsonb),
    ('match_women_top_11', '["basic","daily","minimal","casual"]'::jsonb),
    ('match_women_top_13', '["cozy","feminine","daily","minimal"]'::jsonb),
    ('match_women_top_14', '["minimal","chic","modern","basic"]'::jsonb),
    ('match_women_top_15', '["chic","feminine","modern"]'::jsonb),
    ('match_women_top_16', '["basic","minimal","daily","casual"]'::jsonb)
  ) AS expected(id, tags) ON expected.id = mi.id
  WHERE mi.style_tags = expected.tags AND mi.owner_user_id IS NULL AND mi.project_id IS NULL;
  IF matched <> 60 THEN
    RAISE EXCEPTION '재태깅 검증 실패: 기대 60건, 실제 %건 — 트랜잭션을 롤백합니다', matched;
  END IF;
END $$;

COMMIT;
