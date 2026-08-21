import { useCallback, useMemo } from 'react';
import { FaceLivenessDetectorCore } from '@aws-amplify/ui-react-liveness';
import '@aws-amplify/ui-react/styles.css';

export function FaceLivenessStep({ session, onAnalysisComplete, onCancel, onError }) {
  const credentialProvider = useCallback(async () => ({
    accessKeyId: session.credentials.accessKeyId,
    secretAccessKey: session.credentials.secretAccessKey,
    sessionToken: session.credentials.sessionToken,
    expiration: new Date(session.credentials.expiration),
  }), [session]);
  const config = useMemo(() => ({ credentialProvider }), [credentialProvider]);

  return (
    <FaceLivenessDetectorCore
      sessionId={session.sessionId}
      region="us-east-1"
      config={config}
      onAnalysisComplete={onAnalysisComplete}
      onUserCancel={onCancel}
      onError={onError}
    />
  );
}

export default FaceLivenessStep;
