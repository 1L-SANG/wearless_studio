-- AI 재태깅 제안 검수 후 수동 실행. 이 파일을 생성한 스크립트는 DB에 접속하지 않습니다.
BEGIN;

UPDATE matching_items SET style_tags = '["basic","casual","preppy","classic"]'::jsonb WHERE id = 'match_men_bottom_01';

UPDATE matching_items SET style_tags = '["casual","minimal","trendy"]'::jsonb WHERE id = 'match_men_bottom_02';

UPDATE matching_items SET style_tags = '["casual","daily","cozy","athleisure"]'::jsonb WHERE id = 'match_men_bottom_03';

UPDATE matching_items SET style_tags = '["workwear","vintage","casual","street"]'::jsonb WHERE id = 'match_men_bottom_04';

UPDATE matching_items SET style_tags = '["casual","vintage","street"]'::jsonb WHERE id = 'match_men_bottom_05';

UPDATE matching_items SET style_tags = '["casual","vintage","street","y2k"]'::jsonb WHERE id = 'match_men_bottom_06';

UPDATE matching_items SET style_tags = '["basic","casual","preppy","classic"]'::jsonb WHERE id = 'match_men_bottom_07';

UPDATE matching_items SET style_tags = '["casual","preppy","minimal"]'::jsonb WHERE id = 'match_men_bottom_08';

UPDATE matching_items SET style_tags = '["cozy","casual","daily"]'::jsonb WHERE id = 'match_men_bottom_09';

UPDATE matching_items SET style_tags = '["minimal","sophisticated","modern"]'::jsonb WHERE id = 'match_men_bottom_10';

UPDATE matching_items SET style_tags = '["casual","street","vintage","y2k"]'::jsonb WHERE id = 'match_men_bottom_11';

UPDATE matching_items SET style_tags = '["basic","minimal","formal","sophisticated"]'::jsonb WHERE id = 'match_men_bottom_12';

UPDATE matching_items SET style_tags = '["casual","workwear","vintage","street"]'::jsonb WHERE id = 'match_men_bottom_13';

UPDATE matching_items SET style_tags = '["street","vintage","casual"]'::jsonb WHERE id = 'match_men_bottom_14';

UPDATE matching_items SET style_tags = '["casual","sporty","athleisure","daily"]'::jsonb WHERE id = 'match_men_bottom_15';

UPDATE matching_items SET style_tags = '["casual","daily","vintage"]'::jsonb WHERE id = 'match_women_bottom_02';

UPDATE matching_items SET style_tags = '["casual","daily","vintage"]'::jsonb WHERE id = 'match_women_bottom_03';

UPDATE matching_items SET style_tags = '["casual","daily","vintage"]'::jsonb WHERE id = 'match_women_bottom_04';

UPDATE matching_items SET style_tags = '["minimal","sophisticated","modern","classic"]'::jsonb WHERE id = 'match_women_bottom_05';

UPDATE matching_items SET style_tags = '["casual","street","vintage"]'::jsonb WHERE id = 'match_women_bottom_06';

UPDATE matching_items SET style_tags = '["minimal","casual","cozy"]'::jsonb WHERE id = 'match_women_bottom_07';

UPDATE matching_items SET style_tags = '["casual","athleisure","cozy","daily"]'::jsonb WHERE id = 'match_women_bottom_08';

UPDATE matching_items SET style_tags = '["minimal","sophisticated","modern","casual"]'::jsonb WHERE id = 'match_women_bottom_09';

UPDATE matching_items SET style_tags = '["casual","street","vintage"]'::jsonb WHERE id = 'match_women_bottom_10';

UPDATE matching_items SET style_tags = '["minimal","casual","trendy"]'::jsonb WHERE id = 'match_women_bottom_11';

UPDATE matching_items SET style_tags = '["minimal","chic","sophisticated","formal"]'::jsonb WHERE id = 'match_women_bottom_12';

UPDATE matching_items SET style_tags = '["casual","cozy","vintage"]'::jsonb WHERE id = 'match_women_bottom_13';

UPDATE matching_items SET style_tags = '["cozy","casual","minimal"]'::jsonb WHERE id = 'match_women_bottom_14';

UPDATE matching_items SET style_tags = '["preppy","lovely","casual"]'::jsonb WHERE id = 'match_women_bottom_15';

UPDATE matching_items SET style_tags = '["chic","modern","sophisticated","classic"]'::jsonb WHERE id = 'match_women_bottom_16';

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_01';

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_02';

UPDATE matching_items SET style_tags = '["basic","daily","casual","minimal"]'::jsonb WHERE id = 'match_men_top_03';

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_04';

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_05';

UPDATE matching_items SET style_tags = '["classic","minimal","casual","preppy"]'::jsonb WHERE id = 'match_men_top_06';

UPDATE matching_items SET style_tags = '["casual","preppy","retro"]'::jsonb WHERE id = 'match_men_top_07';

UPDATE matching_items SET style_tags = '["casual","cozy","daily"]'::jsonb WHERE id = 'match_men_top_08';

UPDATE matching_items SET style_tags = '["casual","retro","daily"]'::jsonb WHERE id = 'match_men_top_10';

UPDATE matching_items SET style_tags = '["minimal","casual","classic","preppy"]'::jsonb WHERE id = 'match_men_top_11';

UPDATE matching_items SET style_tags = '["basic","minimal","casual"]'::jsonb WHERE id = 'match_men_top_12';

UPDATE matching_items SET style_tags = '["casual","cozy","daily"]'::jsonb WHERE id = 'match_men_top_13';

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_14';

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_15';

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_men_top_16';

UPDATE matching_items SET style_tags = '["casual","cozy","vintage"]'::jsonb WHERE id = 'match_women_top_01';

UPDATE matching_items SET style_tags = '["casual","daily","classic","preppy"]'::jsonb WHERE id = 'match_women_top_02';

UPDATE matching_items SET style_tags = '["basic","daily","casual","minimal"]'::jsonb WHERE id = 'match_women_top_03';

UPDATE matching_items SET style_tags = '["basic","minimal","casual","preppy"]'::jsonb WHERE id = 'match_women_top_04';

UPDATE matching_items SET style_tags = '["casual","cozy","vintage"]'::jsonb WHERE id = 'match_women_top_05';

UPDATE matching_items SET style_tags = '["basic","daily","casual","cozy"]'::jsonb WHERE id = 'match_women_top_06';

UPDATE matching_items SET style_tags = '["basic","daily","casual","minimal"]'::jsonb WHERE id = 'match_women_top_07';

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_women_top_08';

UPDATE matching_items SET style_tags = '["basic","daily","minimal","cozy"]'::jsonb WHERE id = 'match_women_top_09';

UPDATE matching_items SET style_tags = '["minimal","classic","sophisticated","casual"]'::jsonb WHERE id = 'match_women_top_10';

UPDATE matching_items SET style_tags = '["basic","daily","minimal","casual"]'::jsonb WHERE id = 'match_women_top_11';

UPDATE matching_items SET style_tags = '["cozy","feminine","daily","minimal"]'::jsonb WHERE id = 'match_women_top_13';

UPDATE matching_items SET style_tags = '["minimal","chic","modern","basic"]'::jsonb WHERE id = 'match_women_top_14';

UPDATE matching_items SET style_tags = '["chic","feminine","modern"]'::jsonb WHERE id = 'match_women_top_15';

UPDATE matching_items SET style_tags = '["basic","minimal","daily","casual"]'::jsonb WHERE id = 'match_women_top_16';

-- 건수 검증: 아래 결과가 60인지 확인한 뒤 COMMIT 결과를 승인하세요.
SELECT COUNT(*) AS retagged_item_count
FROM matching_items
WHERE id IN (
  'match_men_bottom_01',
  'match_men_bottom_02',
  'match_men_bottom_03',
  'match_men_bottom_04',
  'match_men_bottom_05',
  'match_men_bottom_06',
  'match_men_bottom_07',
  'match_men_bottom_08',
  'match_men_bottom_09',
  'match_men_bottom_10',
  'match_men_bottom_11',
  'match_men_bottom_12',
  'match_men_bottom_13',
  'match_men_bottom_14',
  'match_men_bottom_15',
  'match_women_bottom_02',
  'match_women_bottom_03',
  'match_women_bottom_04',
  'match_women_bottom_05',
  'match_women_bottom_06',
  'match_women_bottom_07',
  'match_women_bottom_08',
  'match_women_bottom_09',
  'match_women_bottom_10',
  'match_women_bottom_11',
  'match_women_bottom_12',
  'match_women_bottom_13',
  'match_women_bottom_14',
  'match_women_bottom_15',
  'match_women_bottom_16',
  'match_men_top_01',
  'match_men_top_02',
  'match_men_top_03',
  'match_men_top_04',
  'match_men_top_05',
  'match_men_top_06',
  'match_men_top_07',
  'match_men_top_08',
  'match_men_top_10',
  'match_men_top_11',
  'match_men_top_12',
  'match_men_top_13',
  'match_men_top_14',
  'match_men_top_15',
  'match_men_top_16',
  'match_women_top_01',
  'match_women_top_02',
  'match_women_top_03',
  'match_women_top_04',
  'match_women_top_05',
  'match_women_top_06',
  'match_women_top_07',
  'match_women_top_08',
  'match_women_top_09',
  'match_women_top_10',
  'match_women_top_11',
  'match_women_top_13',
  'match_women_top_14',
  'match_women_top_15',
  'match_women_top_16'
);

COMMIT;
