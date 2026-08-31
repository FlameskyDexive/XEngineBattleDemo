Shader "Zonezero/Toon"

Variants
{
    Variant("HAS_TANGENTS")
    Variant("SKINNED")
    Variant("HAS_BONEINDICES")
    Variant("HAS_BONEWEIGHTS")
}

// Cel/toon shading ported from the ZZZ reference project's "YSA Toon/Lit" Unity shader graph
// (see docs/superpowers/specs/2026-08-21-zonezero-combat-demo.md, milestone M2). M2 scope:
// main-light banding + shadow receiving + flat ambient + additional-light banding + position
// gradient tint + alpha clipping. Rim / specular / emissive / face normals / outline land in M3
// on the same ToonShadingMaterial cbuffer (fields append at the tail).
//
// Formulas mirror Lights Calculation.hlsl from the reference:
//   band  = smoothstep(midPoint, midPoint + smoothness, NdotL)
//   shadow= smoothstep(0, shadowSmoothness, atten) -> max(shadow, 1 - power) -> * occlusion
//   color = albedo * lerp(_Ambient, mainLightColor, band * shadow) + additionalBanded + ...
// Light intensity is Unity-parity RAW (`GetMainLight().color` == color * intensity): the
// legacy engine-wide "* 8" ForwardLightManager compensation is deliberately NOT applied
// here — with it, any lit texel saturates to white and the imported Unity scene light
// (intensity 1) stops matching the reference render (2026-08-27 toon alignment pass).
// The engine light fetch (directional uniform + BVH/cluster additional lights) matches
// Default/Standard via Lighting.glsl (GLSL) and StandardLighting.hlsl (Slang).

Properties
{
    _MainTex ("Albedo", Texture2D) = "white"
    _MainColor ("Tint", Color) = (1.0, 1.0, 1.0, 1.0)
    _Tiling ("Tiling", Vector2) = (1.0, 1.0)
    _Offset ("Offset", Vector2) = (0.0, 0.0)
    _AlphaCutoff ("Alpha Cutoff", Float) = 0.5

    _Ambient ("Shadow Ambient Color", Color) = (0.2547, 0.2547, 0.2547, 1.0)
    _MainLightMidPoint ("Main Light Mid Point", Float) = 0.0
    _MainLightSmoothness ("Main Light Smoothness", Float) = 0.1
    _ReceiveShadowsEnabled ("Receive Main Light Shadows", Float) = 1.0
    _MainShadowsPower ("Main Shadows Power", Float) = 0.5
    _ShadowSmoothness ("Shadow Smoothness", Float) = 1.0

    _OcclusionMap ("Occlusion (R)", Texture2D) = "white"
    _OcclusionPower ("Occlusion Power", Float) = 1.0

    _AdditionalLightsEnabled ("Additional Lights", Float) = 1.0
    _AdditionalLightsMidPoint ("Additional Lights Mid Point", Float) = 0.0
    _AdditionalLightsSmoothness ("Additional Lights Smoothness", Float) = 1.0
    _AdditionalLightShadows ("Additional Light Shadows", Float) = 1.0
    _AdditionalShadowsPower ("Additional Shadows Power", Float) = 1.0

    _GradientEnabled ("Gradient Enabled", Float) = 0.0
    _GColor1 ("Gradient Color 1", Color) = (0.0, 0.0, 0.0, 1.0)
    _GColor2 ("Gradient Color 2", Color) = (1.0, 1.0, 1.0, 1.0)
    _GradientMultiplier ("Gradient Multiplier", Float) = 1.0
    _GradientOffset ("Gradient Offset", Float) = 0.0
    _InverseColors ("Inverse Gradient Colors", Float) = 0.0
    _GradientDirection ("Gradient Direction (0=X 1=Y 2=Z)", Float) = 1.0

    _RimColor ("Rim Color (A=intensity)", Color) = (1.0, 0.0, 0.0, 1.0)
    _RimEnabled ("Rim Enabled", Float) = 0.0
    _RimMidPoint ("Rim Mid Point", Float) = 0.6
    _RimSmoothness ("Rim Smoothness", Float) = 0.1
    _DynamicRemap ("Dynamic Remap", Float) = 0.5
    _RimHideOnShadow ("Rim Hide On Shadow", Float) = 1.0

    _MainLightSpecular ("Main Light Specular", Float) = 0.0
    _AdditionalLightsSpecular ("Additional Lights Specular", Float) = 0.0
    _SpecularMap ("Specular Glossiness Map (RGB bias)", Texture2D) = "white"
    _SpecularMapPower ("Specular Map Power", Float) = 0.0
    _SpecularMidPoint ("Specular Mid Point", Float) = 0.9
    _SpecularSmoothness ("Specular Smoothness", Float) = 0.1
    _SpecularTint ("Specular Tint (A=intensity)", Color) = (1.0, 1.0, 1.0, 1.0)
    _SpecularCustomizeEnabled ("Specular Customize Enabled", Float) = 0.0
    _Specular_Texture ("Specular Customize Texture", Texture2D) = "white"
    _SpecularHideOnShadows ("Specular Hide On Shadows", Float) = 1.0
    _SpecularTextureColor ("Specular Texture Color", Color) = (1.0, 1.0, 1.0, 1.0)

    _EmissionTex ("Emission", Texture2D) = "black"
    _EmissionColor ("Emission Color", Color) = (1.0, 1.0, 1.0, 1.0)
    _Emission ("Emission Enabled", Float) = 0.0

    _IsFace ("Is Face (position-derived normals)", Float) = 0.0
    _FaceX ("Face X (x=modifier y=posMultiplier z=offset)", Vector4) = (0.0, 10.0, 0.0, 0.0)
    _FaceY ("Face Y (x=modifier y=posMultiplier z=offset)", Vector4) = (0.0, 10.0, 0.0, 0.0)
    _FaceZ ("Face Z (x=modifier y=posMultiplier z=offset)", Vector4) = (0.0, 10.0, 0.0, 0.0)

    _ShrinkSize ("Shrink Size (lit mesh inward, negative)", Float) = 0.0
}

Pass "Toon"
{
    Tags { "RenderOrder" = "Opaque" }
    Cull Back

	GLSLPROGRAM

		Vertex
		{
            #include "XEngineCG"
            #include "VertexAttributes"

			out vec2 texCoord0;
			out vec3 worldPos;
			out vec3 objectPos;
			out vec3 vNormal;

			uniform vec2 _Tiling;
			uniform vec2 _Offset;
			uniform float _ShrinkSize;

			void main()
			{
				vec3 worldPosition = TransformPosition(vertexPosition);
				vec3 worldNormal = TransformDirection(GetMorphedNormal(vertexNormal));
				if (_ShrinkSize != 0.0) {
					// YSA CameraFixMultiplier: screen-depth-independent width factor. Perspective
					// scales with |viewZ| * fovY * 1e-4; orthographic with orthoHeight * 125 * 1e-4.
					vec4 clip = xengine_MatVP * vec4(worldPosition, 1.0);
					float cameraFix;
					if (xengine_MatP[3][3] < 0.5) {
						float fovY = 2.0 * atan(1.0 / xengine_MatP[1][1]) * 57.2957795;
						cameraFix = clamp(abs(clip.w), 0.0, 4.0) * fovY * 0.0001;
					} else {
						float orthoHeight = 1.0 / xengine_MatP[1][1];
						cameraFix = orthoHeight * 125.0 * 0.0001;
					}
					vec3 shrunk = worldPosition + normalize(worldNormal) * (_ShrinkSize * cameraFix);
					worldPosition = (xengine_WorldToObject * vec4(shrunk, 1.0)).xyz;
					objectPos = worldPosition;
					worldPos = shrunk;
					gl_Position = TransformClip(worldPosition);
				} else {
					objectPos = vertexPosition;
					worldPos = worldPosition;
					gl_Position = TransformClip(worldPosition);
				}
				texCoord0 = vertexTexCoord0 * _Tiling + _Offset;
				vNormal = worldNormal;
			}
		}

		Fragment
		{
            #define XENGINE_CLUSTERED_LIGHTS
            #include "XEngineCG"
            #include "Lighting"

			layout (location = 0) out vec4 fragColor;

			in vec2 texCoord0;
			in vec3 worldPos;
			in vec3 objectPos;
			in vec3 vNormal;

			uniform sampler2D _MainTex;
			uniform vec4 _MainColor;
			uniform float _AlphaCutoff;

			uniform sampler2D _OcclusionMap;
			uniform vec4 _Ambient;
			uniform float _MainLightMidPoint;
			uniform float _MainLightSmoothness;
			uniform float _ReceiveShadowsEnabled;
			uniform float _MainShadowsPower;
			uniform float _ShadowSmoothness;
			uniform float _OcclusionPower;
			uniform float _AdditionalLightsEnabled;
			uniform float _AdditionalLightsMidPoint;
			uniform float _AdditionalLightsSmoothness;
			uniform float _AdditionalLightShadows;
			uniform float _AdditionalShadowsPower;
			uniform float _GradientEnabled;
			uniform vec4 _GColor1;
			uniform vec4 _GColor2;
			uniform float _GradientMultiplier;
			uniform float _GradientOffset;
			uniform float _InverseColors;
			uniform float _GradientDirection;

			uniform vec4 _RimColor;
			uniform float _RimEnabled;
			uniform float _RimMidPoint;
			uniform float _RimSmoothness;
			uniform float _DynamicRemap;
			uniform float _RimHideOnShadow;

			uniform float _MainLightSpecular;
			uniform float _AdditionalLightsSpecular;
			uniform sampler2D _SpecularMap;
			uniform float _SpecularMapPower;
			uniform float _SpecularMidPoint;
			uniform float _SpecularSmoothness;
			uniform vec4 _SpecularTint;
			uniform float _SpecularCustomizeEnabled;
			uniform sampler2D _Specular_Texture;
			uniform float _SpecularHideOnShadows;
			uniform vec4 _SpecularTextureColor;

			uniform sampler2D _EmissionTex;
			uniform vec4 _EmissionColor;
			uniform float _Emission;

			uniform float _IsFace;
			uniform vec4 _FaceX;
			uniform vec4 _FaceY;
			uniform vec4 _FaceZ;

			// Main-light shadow term in [0,1]: cascade sample -> screen-space -> smoothstep ->
			// max(shadow, 1 - power) floor -> occlusion map. Mirrors the reference's
			// MainShadowsCalculator (occlusion folds into the same multiplier).
			float ToonMainShadow(vec3 n)
			{
				float atten = 1.0;
				if (_DirectionalLightShadowEnabled != 0)
					atten = ApplyScreenSpaceShadows(1.0 - SampleDirectionalShadow(worldPos, n));
				float s = smoothstep(0.0, max(_ShadowSmoothness, 1e-4), atten);
				s = max(s, 1.0 - _MainShadowsPower);
				float occ = texture(_OcclusionMap, texCoord0).r;
				return s * mix(1.0, occ, _OcclusionPower);
			}

			// Banded contribution of one additional light: same smoothstep band as the reference
			// AdditionalLightingCalcualtor, engine attenuation window, optional per-light shadows,
			// optional banded Blinn specular (SpecularAdditionalLightingCalculation).
			vec3 ToonEvaluateLocalLight(LightSample L, vec3 n, vec3 viewDir, vec3 albedo)
			{
				if (!LightAffectsObject(L.RenderingLayers)) return vec3(0.0);

				vec3 lightToPixel = worldPos - L.Position;
				float dist2 = dot(lightToPixel, lightToPixel);
				float dist = sqrt(dist2);
				vec3 lightDir = -lightToPixel * (1.0 / max(dist, 1e-6));

				float spotAxisCos = 0.0;
				if (L.Type == 2) {
					spotAxisCos = dot(normalize(L.Direction), -lightDir);
					if (spotAxisCos <= L.SpotCos) return vec3(0.0);
				}

				float ndl = dot(n, lightDir);
				float band = smoothstep(_AdditionalLightsMidPoint,
				                         _AdditionalLightsMidPoint + _AdditionalLightsSmoothness,
				                         ndl);

				float invR2 = 1.0 / (L.Range * L.Range);
				float factor = dist2 * invR2;
				float window = clamp(1.0 - factor * factor, 0.0, 1.0);
				window *= window;
				float attenuation = (1.0 / max(dist2, 0.01)) * window;
				if (L.Type == 2)
					attenuation *= smoothstep(L.SpotCos, L.InnerSpotCos, spotAxisCos);
				if (attenuation <= 0.0001) return vec3(0.0);

				float shadowFactor = 1.0;
				if (_AdditionalLightShadows > 0.5 && L.ShadowEnabled != 0 && L.ShadowSlot >= 0) {
					float shadow = (L.Type == 1)
						? SamplePointShadow(L, L.ShadowSlot, worldPos, n)
						: SampleSpotShadow(L, L.ShadowSlot, worldPos, n);
					shadowFactor = smoothstep(0.0, max(_ShadowSmoothness, 1e-4), 1.0 - shadow);
					shadowFactor = max(shadowFactor, 1.0 - _AdditionalShadowsPower);
				}

				vec3 lightTerm = saturate(L.Color * L.Intensity * band * attenuation * shadowFactor) * albedo;
				if (_AdditionalLightsSpecular > 0.5) {
					vec3 h = normalize(viewDir + lightDir);
					float spec = smoothstep(_SpecularMidPoint,
					                        _SpecularMidPoint + _SpecularSmoothness,
					                        dot(n, h));
					lightTerm += saturate(spec) * _SpecularTint.rgb * _SpecularTint.a
					           * L.Color * L.Intensity * attenuation * shadowFactor;
				}
				return lightTerm;
			}

			vec3 ToonAdditionalLights(vec3 n, vec3 viewDir, vec3 albedo)
			{
				vec3 total = vec3(0.0);
			#ifdef XENGINE_CLUSTERED_LIGHTS
				if (_ClusterGridParams.w > 0.5) {
					ivec2 header = Cluster_Header(worldPos);
					for (int i = 0; i < header.y; i++) {
						LightSample L = Cluster_FetchLight(header.x + i);
						total += ToonEvaluateLocalLight(L, n, viewDir, albedo);
					}
					return total;
				}
			#endif
				if (_StaticLightRoot >= 0) {
					LBVH_Iter it;
					LBVH_Begin(it, _StaticLightRoot);
					int slot;
					while ((slot = LBVH_Next(it, _StaticLightNodes, _StaticNodeTexSize, _StaticNodeTexShift, worldPos)) >= 0) {
						LightSample L = LBVH_FetchLight(_StaticLightData, _StaticLightTexSize, _StaticLightTexShift, slot);
						total += ToonEvaluateLocalLight(L, n, viewDir, albedo);
					}
				}
				if (_DynamicLightRoot >= 0) {
					LBVH_Iter it;
					LBVH_Begin(it, _DynamicLightRoot);
					int slot;
					while ((slot = LBVH_Next(it, _DynamicLightNodes, _DynamicNodeTexSize, _DynamicNodeTexShift, worldPos)) >= 0) {
						LightSample L = LBVH_FetchLight(_DynamicLightData, _DynamicLightTexSize, _DynamicLightTexShift, slot);
						total += ToonEvaluateLocalLight(L, n, viewDir, albedo);
					}
				}
				return total;
			}

			// Two-color ramp over object-space position (the reference's gradient tint; no ramp
			// texture — axis select via _GradientDirection, swap via _InverseColors).
			vec3 ToonGradientTint()
			{
				float axisPos = _GradientDirection < 0.5 ? objectPos.x
				              : (_GradientDirection < 1.5 ? objectPos.y : objectPos.z);
				float t = clamp(axisPos * _GradientMultiplier + _GradientOffset, 0.0, 1.0);
				vec3 c1 = _InverseColors > 0.5 ? _GColor2.rgb : _GColor1.rgb;
				vec3 c2 = _InverseColors > 0.5 ? _GColor1.rgb : _GColor2.rgb;
				return mix(c1, c2, t);
			}

			void main()
			{
				// Engine convention (mirrors Default/Standard): textures are stored without
				// hardware sRGB views, shaders decode manually. Skipping the decode lifted
				// dark texels ~3x (measured 35/255 -> ~92 on screen) — the milky "white veil".
				vec4 mainSample = texture(_MainTex, texCoord0);
				vec4 base = vec4(gammaToLinearSpace(mainSample.rgb * _MainColor.rgb),
				                 mainSample.a * _MainColor.a);
				if (base.a < _AlphaCutoff)
					discard;
				if (_GradientEnabled > 0.5)
					base.rgb *= ToonGradientTint();

				vec3 n = normalize(vNormal);
				// Face shading: replace the normal with a spherical position-derived one so the
				// face shades like a smooth head (YSA FaceNormalsGenerator, procedural — no face
				// shadow map). Per-axis blend keeps cheeks/chin controllable via the modifiers.
				if (_IsFace > 0.5) {
					vec3 sphereDir = (objectPos + vec3(_FaceX.z, _FaceY.z, _FaceZ.z))
					               * vec3(_FaceX.y, _FaceY.y, _FaceZ.y);
					vec3 faceN = normalize(TransformDirection(normalize(sphereDir)));
					n = normalize(mix(n, faceN, clamp(vec3(_FaceX.x, _FaceY.x, _FaceZ.x), 0.0, 1.0)));
				}

				vec3 viewDir = normalize(_WorldSpaceCameraPos.xyz - worldPos);
				vec3 lightDir = vec3(0.0);
				float ndl = 0.0;
				vec3 lightColor = vec3(0.0);
				if (_DirectionalLightEnabled != 0) {
					lightDir = normalize(_DirectionalLightDirection);
					ndl = dot(n, lightDir);
					lightColor = _DirectionalLightColor * _DirectionalLightIntensity;
				}

				float mainBand = smoothstep(_MainLightMidPoint,
				                            _MainLightMidPoint + _MainLightSmoothness,
				                            ndl);
				float L = _ReceiveShadowsEnabled > 0.5 ? mainBand * ToonMainShadow(n) : mainBand;
				vec3 color = base.rgb * mix(_Ambient.rgb, lightColor, L);

				// --- Main-light H-offset specular (YSA SpecularMainLightingCalculation): the
				// glossiness map remaps 0..1 -> -1..1 as a PER-CHANNEL bias on the half vector.
				vec3 maxTerm = vec3(0.0);
				if (_MainLightSpecular > 0.5 && _DirectionalLightEnabled != 0) {
					vec3 specBias = mix(vec3(0.0), texture(_SpecularMap, texCoord0).rgb * 2.0 - 1.0,
					                    _SpecularMapPower);
					vec3 h = normalize(viewDir + lightDir) + specBias;
					float spec = smoothstep(_SpecularMidPoint,
					                        _SpecularMidPoint + _SpecularSmoothness,
					                        dot(n, h));
					maxTerm = max(maxTerm, saturate(spec) * _SpecularTint.rgb * _SpecularTint.a
					              * lightColor * L);
				}

				// --- Rim (YSA RimLightingCalculation): threshold shifts with lighting via
				// _DynamicRemap; composited through max() then added, optional shadow gating.
				if (_RimEnabled > 0.5) {
					float rimDot = 1.0 - clamp(dot(viewDir, n), 0.0, 1.0);
					float factor = mix(1.0, ndl, _DynamicRemap);
					float threshold = mix(1.0, _RimMidPoint, factor);
					float rim = smoothstep(threshold, threshold + _RimSmoothness, rimDot);
					vec3 rimColor = saturate(rim) * _RimColor.rgb * _RimColor.a;
					maxTerm = max(maxTerm, mix(rimColor, rimColor * L, _RimHideOnShadow));
				}

				// --- Custom specular color path (texture-driven, YSA Specular Customize).
				if (_SpecularCustomizeEnabled > 0.5) {
					float specR = _SpecularTextureColor.r;
					vec3 custom = specR * (_SpecularTextureColor.rgb * texture(_Specular_Texture, texCoord0).rgb);
					maxTerm = max(maxTerm, mix(custom, custom * L, _SpecularHideOnShadows));
				}
				color += maxTerm;

				if (_AdditionalLightsEnabled > 0.5)
					color += ToonAdditionalLights(n, viewDir, base.rgb);

				if (_Emission > 0.5)
					color += texture(_EmissionTex, texCoord0).rgb * _EmissionColor.rgb;

				color = ApplyFog(color, worldPos);
				fragColor = vec4(color, 1.0);
			}
		}
	ENDGLSL

	SLANGPROGRAM
		Vertex
		{
			#include "XEngineCG"
			#include "VertexAttributes"

			cbuffer ToonShadingMaterial : register(b2)
			{
				float2 _Tiling;
				float2 _Offset;
				float4 _MainColor;
				float4 _Ambient;
				float4 _GColor1;
				float4 _GColor2;
				float _MainLightMidPoint;
				float _MainLightSmoothness;
				float _ReceiveShadowsEnabled;
				float _MainShadowsPower;
				float _ShadowSmoothness;
				float _OcclusionPower;
				float _AdditionalLightsEnabled;
				float _AdditionalLightsMidPoint;
				float _AdditionalLightsSmoothness;
				float _AdditionalLightShadows;
				float _AdditionalShadowsPower;
				float _GradientEnabled;
				float _GradientMultiplier;
				float _GradientOffset;
				float _InverseColors;
				float _GradientDirection;
				float _AlphaCutoff;
				// M2 tail padding: the M3 vector rows must stay 16-byte aligned (C# marshals a
				// Float4 at 4-byte alignment, so each vector member follows a full scalar row).
				float _ToonPad0;
				float _ToonPad1;
				float _ToonPad2;
				float4 _RimColor;
				float4 _EmissionColor;
				float4 _SpecularTint;
				float4 _SpecularTextureColor;
				float _RimEnabled;
				float _RimMidPoint;
				float _RimSmoothness;
				float _DynamicRemap;
				float _RimHideOnShadow;
				float _MainLightSpecular;
				float _AdditionalLightsSpecular;
				float _SpecularMapPower;
				float _SpecularMidPoint;
				float _SpecularSmoothness;
				float _SpecularCustomizeEnabled;
				float _SpecularHideOnShadows;
				float _Emission;
				float _IsFace;
				float _ShrinkSize;
				float _ToonPad3;
				float4 _FaceX;
				float4 _FaceY;
				float4 _FaceZ;
			};

			struct VSInput
			{
				float3 vertexPosition : POSITION;
				float2 vertexTexCoord0 : TEXCOORD0;
				float3 vertexNormal : NORMAL;
#ifdef HAS_BONEINDICES
				float4 vertexBoneIndices : BLENDINDICES;
				float4 vertexBoneWeights : BLENDWEIGHT;
#endif
			};

			struct VSOutput
			{
				float4 position : SV_Position;
				float2 texCoord0 : TEXCOORD0;
				float3 worldPos : TEXCOORD1;
				float3 objectPos : TEXCOORD2;
				float3 vNormal : TEXCOORD3;
			};

			VSOutput main(VSInput input)
			{
				VSOutput o;
				float3 objectPos = input.vertexPosition;
#if defined(SKINNED) && defined(HAS_BONEINDICES)
				float3 worldPosition = TransformPositionSkinned(objectPos, input.vertexBoneIndices, input.vertexBoneWeights);
#else
				float3 worldPosition = TransformPosition(objectPos);
#endif
#if defined(SKINNED) && defined(HAS_BONEINDICES)
				float3 worldNormal = normalize(mul((float3x3)XENGINE_MATRIX_M, GetSkinnedNormal(GetMorphedNormal(input.vertexNormal), input.vertexBoneIndices, input.vertexBoneWeights)));
#else
				float3 worldNormal = TransformDirection(GetMorphedNormal(input.vertexNormal));
#endif
				if (_ShrinkSize != 0.0)
				{
					// YSA CameraFixMultiplier — see the GLSL twin for the formula commentary.
					float4 clip = mul(XENGINE_MATRIX_VP, float4(worldPosition, 1.0));
					float cameraFix;
					if (XENGINE_MATRIX_P[3][3] < 0.5)
					{
						float fovY = 2.0 * atan(1.0 / XENGINE_MATRIX_P[1][1]) * 57.2957795;
						cameraFix = clamp(abs(clip.w), 0.0, 4.0) * fovY * 0.0001;
					}
					else
					{
						float orthoHeight = 1.0 / XENGINE_MATRIX_P[1][1];
						cameraFix = orthoHeight * 125.0 * 0.0001;
					}
					float3 shrunk = worldPosition + normalize(worldNormal) * (_ShrinkSize * cameraFix);
					objectPos = mul(xengine_WorldToObject, float4(shrunk, 1.0)).xyz;
					worldPosition = shrunk;
				}
#if defined(SKINNED) && defined(HAS_BONEINDICES)
				o.position = TransformClipSkinned(objectPos, input.vertexBoneIndices, input.vertexBoneWeights);
#else
				o.position = TransformClip(objectPos);
#endif
				o.texCoord0 = input.vertexTexCoord0 * _Tiling + _Offset;
				o.worldPos = worldPosition;
				o.objectPos = objectPos;
				o.vNormal = worldNormal;
				return o;
			}
		}

		Fragment
		{
			#define XENGINE_CLUSTERED_LIGHTS
			#include "XEngineCG"
			#include "StandardLighting"

			cbuffer ToonShadingMaterial : register(b2)
			{
				float2 _Tiling;
				float2 _Offset;
				float4 _MainColor;
				float4 _Ambient;
				float4 _GColor1;
				float4 _GColor2;
				float _MainLightMidPoint;
				float _MainLightSmoothness;
				float _ReceiveShadowsEnabled;
				float _MainShadowsPower;
				float _ShadowSmoothness;
				float _OcclusionPower;
				float _AdditionalLightsEnabled;
				float _AdditionalLightsMidPoint;
				float _AdditionalLightsSmoothness;
				float _AdditionalLightShadows;
				float _AdditionalShadowsPower;
				float _GradientEnabled;
				float _GradientMultiplier;
				float _GradientOffset;
				float _InverseColors;
				float _GradientDirection;
				float _AlphaCutoff;
				// M2 tail padding: the M3 vector rows must stay 16-byte aligned (C# marshals a
				// Float4 at 4-byte alignment, so each vector member follows a full scalar row).
				float _ToonPad0;
				float _ToonPad1;
				float _ToonPad2;
				float4 _RimColor;
				float4 _EmissionColor;
				float4 _SpecularTint;
				float4 _SpecularTextureColor;
				float _RimEnabled;
				float _RimMidPoint;
				float _RimSmoothness;
				float _DynamicRemap;
				float _RimHideOnShadow;
				float _MainLightSpecular;
				float _AdditionalLightsSpecular;
				float _SpecularMapPower;
				float _SpecularMidPoint;
				float _SpecularSmoothness;
				float _SpecularCustomizeEnabled;
				float _SpecularHideOnShadows;
				float _Emission;
				float _IsFace;
				float _ShrinkSize;
				float _ToonPad3;
				float4 _FaceX;
				float4 _FaceY;
				float4 _FaceZ;
			};

			Texture2D _MainTex : register(t0);
			SamplerState _MainTexSampler : register(s0);
			Texture2D _OcclusionMap : register(t1);
			SamplerState _OcclusionMapSampler : register(s1);
			Texture2D _EmissionTex : register(t2);
			SamplerState _EmissionTexSampler : register(s2);
			Texture2D _SpecularMap : register(t3);
			SamplerState _SpecularMapSampler : register(s3);
			// t4-t8/t11/t14 are reserved by StandardLighting (light BVH / atlas / lightmap);
			// t9/t10 stay clear of Standard's material slots for symmetry.
			Texture2D _Specular_Texture : register(t12);
			SamplerState _Specular_TextureSampler : register(s12);

			struct PSInput
			{
				float4 position : SV_Position;
				float2 texCoord0 : TEXCOORD0;
				float3 worldPos : TEXCOORD1;
				float3 objectPos : TEXCOORD2;
				float3 vNormal : TEXCOORD3;
			};

			float ToonMainShadow(PSInput input, float3 n)
			{
				float atten = 1.0;
				if (_ShadowParams0.x != 0.0)
					atten = StandardApplyScreenSpaceShadows(
						1.0 - StandardSampleDirectionalShadow(input.worldPos, n, input.position.xy),
						input.position.xy);
				float s = smoothstep(0.0, max(_ShadowSmoothness, 1e-4), atten);
				s = max(s, 1.0 - _MainShadowsPower);
				float occ = _OcclusionMap.Sample(_OcclusionMapSampler, input.texCoord0).r;
				return s * lerp(1.0, occ, _OcclusionPower);
			}

			float3 ToonEvaluateLocalLight(StandardLightSample L, PSInput input, float3 n, float3 viewDir, float3 albedo)
			{
				if (!LightAffectsObject(L.RenderingLayers))
					return 0.0.xxx;

				float3 lightToPixel = input.worldPos - L.Position;
				float dist2 = dot(lightToPixel, lightToPixel);
				float dist = sqrt(dist2);
				float3 lightDir = -lightToPixel * (1.0 / max(dist, 1e-6));

				float spotAxisCos = 0.0;
				if (L.Type == 2)
				{
					spotAxisCos = dot(normalize(L.Direction), -lightDir);
					if (spotAxisCos <= L.SpotCos)
						return 0.0.xxx;
				}

				float ndl = dot(n, lightDir);
				float band = smoothstep(_AdditionalLightsMidPoint,
				                        _AdditionalLightsMidPoint + _AdditionalLightsSmoothness,
				                        ndl);

				float invR2 = 1.0 / (L.Range * L.Range);
				float factor = dist2 * invR2;
				float window = clamp(1.0 - factor * factor, 0.0, 1.0);
				window *= window;
				float attenuation = (1.0 / max(dist2, 0.01)) * window;
				if (L.Type == 2)
					attenuation *= smoothstep(L.SpotCos, L.InnerSpotCos, spotAxisCos);
				if (attenuation <= 0.0001)
					return 0.0.xxx;

				float shadowFactor = 1.0;
				if (_AdditionalLightShadows > 0.5 && L.ShadowEnabled != 0 && L.ShadowSlot >= 0)
				{
					float shadow = (L.Type == 1)
						? StandardSamplePointShadow(L, input.worldPos, n, input.position.xy)
						: StandardSampleSpotShadow(L, input.worldPos, n, input.position.xy);
					shadowFactor = smoothstep(0.0, max(_ShadowSmoothness, 1e-4), 1.0 - shadow);
					shadowFactor = max(shadowFactor, 1.0 - _AdditionalShadowsPower);
				}

				float3 lightTerm = saturate(L.Color * L.Intensity * band * attenuation * shadowFactor) * albedo;
				if (_AdditionalLightsSpecular > 0.5)
				{
					float3 h = normalize(viewDir + lightDir);
					float spec = smoothstep(_SpecularMidPoint,
					                        _SpecularMidPoint + _SpecularSmoothness,
					                        dot(n, h));
					lightTerm += saturate(spec) * _SpecularTint.rgb * _SpecularTint.a
					           * L.Color * L.Intensity * attenuation * shadowFactor;
				}
				return lightTerm;
			}

			float3 ToonAdditionalLights(PSInput input, float3 n, float3 viewDir, float3 albedo)
			{
				float3 total = 0.0.xxx;
			#ifdef XENGINE_CLUSTERED_LIGHTS
				if (_ClusterGridParams.w > 0.5)
				{
					int2 header = StandardClusterHeader(input.worldPos);
					for (int i = 0; i < header.y; i++)
					{
						StandardLightSample L = StandardClusterFetchLight(header.x + i);
						total += ToonEvaluateLocalLight(L, input, n, viewDir, albedo);
					}
					return total;
				}
			#endif
				if (_StandardLightFlags.y >= 0 && _StandardStaticLightMeta.x > 0 && _StandardStaticLightMeta.z > 0)
				{
					int current = _StandardLightFlags.y;
					int slot;
					while ((slot = StandardBvhNext(current, _StaticLightNodes, _StandardStaticLightMeta.z,
					                               _StandardStaticLightMeta.w, input.worldPos)) >= 0)
					{
						StandardLightSample L = StandardFetchLight(_StaticLightData, _StandardStaticLightMeta.x,
						                                           _StandardStaticLightMeta.y, slot);
						total += ToonEvaluateLocalLight(L, input, n, viewDir, albedo);
					}
				}
				if (_StandardLightFlags.z >= 0 && _StandardDynamicLightMeta.x > 0 && _StandardDynamicLightMeta.z > 0)
				{
					int current = _StandardLightFlags.z;
					int slot;
					while ((slot = StandardBvhNext(current, _DynamicLightNodes, _StandardDynamicLightMeta.z,
					                               _StandardDynamicLightMeta.w, input.worldPos)) >= 0)
					{
						StandardLightSample L = StandardFetchLight(_DynamicLightData, _StandardDynamicLightMeta.x,
						                                           _StandardDynamicLightMeta.y, slot);
						total += ToonEvaluateLocalLight(L, input, n, viewDir, albedo);
					}
				}
				return total;
			}

			float3 ToonGradientTint(PSInput input)
			{
				float axisPos = _GradientDirection < 0.5 ? input.objectPos.x
				              : (_GradientDirection < 1.5 ? input.objectPos.y : input.objectPos.z);
				float t = saturate(axisPos * _GradientMultiplier + _GradientOffset);
				float3 c1 = _InverseColors > 0.5 ? _GColor2.rgb : _GColor1.rgb;
				float3 c2 = _InverseColors > 0.5 ? _GColor1.rgb : _GColor2.rgb;
				return lerp(c1, c2, t);
			}

			float4 main(PSInput input) : SV_Target
			{
				// Engine convention (mirrors Default/Standard): manual sRGB decode — see GLSL twin.
				float4 mainSample = _MainTex.Sample(_MainTexSampler, input.texCoord0);
				float4 base = float4(gammaToLinearSpace(mainSample.rgb * _MainColor.rgb),
				                     mainSample.a * _MainColor.a);
				if (base.a < _AlphaCutoff)
					discard;
				if (_GradientEnabled > 0.5)
					base.rgb *= ToonGradientTint(input);

				float3 n = normalize(input.vNormal);
				// Face shading: spherical position-derived normals (YSA FaceNormalsGenerator).
				if (_IsFace > 0.5)
				{
					float3 sphereDir = (input.objectPos + float3(_FaceX.z, _FaceY.z, _FaceZ.z))
					                 * float3(_FaceX.y, _FaceY.y, _FaceZ.y);
					float3 faceN = normalize(TransformDirection(normalize(sphereDir)));
					n = normalize(lerp(n, faceN, clamp(float3(_FaceX.x, _FaceY.x, _FaceZ.x), 0.0, 1.0)));
				}

				float3 viewDir = normalize(_WorldSpaceCameraPos.xyz - input.worldPos);
				float3 lightDir = 0.0.xxx;
				float ndl = 0.0;
				float3 lightColor = 0.0.xxx;
				if (_StandardLightFlags.x != 0)
				{
					lightDir = normalize(_StandardDirectionalDirectionIntensity.xyz);
					ndl = dot(n, lightDir);
					lightColor = _StandardDirectionalColor.rgb * _StandardDirectionalDirectionIntensity.w;
				}

				float mainBand = smoothstep(_MainLightMidPoint,
				                            _MainLightMidPoint + _MainLightSmoothness,
				                            ndl);
				float L = _ReceiveShadowsEnabled > 0.5 ? mainBand * ToonMainShadow(input, n) : mainBand;
				float3 color = base.rgb * lerp(_Ambient.rgb, lightColor, L);

				// --- Main-light H-offset specular (per-channel glossiness-map bias on H).
				float3 maxTerm = 0.0.xxx;
				if (_MainLightSpecular > 0.5 && _StandardLightFlags.x != 0)
				{
					float3 specBias = lerp(0.0.xxx, _SpecularMap.Sample(_SpecularMapSampler, input.texCoord0).rgb * 2.0 - 1.0,
					                       _SpecularMapPower);
					float3 h = normalize(viewDir + lightDir) + specBias;
					float spec = smoothstep(_SpecularMidPoint,
					                        _SpecularMidPoint + _SpecularSmoothness,
					                        dot(n, h));
					maxTerm = max(maxTerm, saturate(spec) * _SpecularTint.rgb * _SpecularTint.a
					              * lightColor * L);
				}

				// --- Rim (dynamic threshold via _DynamicRemap, shadow-gated, max-composited).
				if (_RimEnabled > 0.5)
				{
					float rimDot = 1.0 - saturate(dot(viewDir, n));
					float factor = lerp(1.0, ndl, _DynamicRemap);
					float threshold = lerp(1.0, _RimMidPoint, factor);
					float rim = smoothstep(threshold, threshold + _RimSmoothness, rimDot);
					float3 rimColor = saturate(rim) * _RimColor.rgb * _RimColor.a;
					maxTerm = max(maxTerm, lerp(rimColor, rimColor * L, _RimHideOnShadow));
				}

				// --- Custom specular color path (texture-driven).
				if (_SpecularCustomizeEnabled > 0.5)
				{
					float specR = _SpecularTextureColor.r;
					float3 custom = specR * (_SpecularTextureColor.rgb * _Specular_Texture.Sample(_Specular_TextureSampler, input.texCoord0).rgb);
					maxTerm = max(maxTerm, lerp(custom, custom * L, _SpecularHideOnShadows));
				}
				color += maxTerm;

				if (_AdditionalLightsEnabled > 0.5)
					color += ToonAdditionalLights(input, n, viewDir, base.rgb);

				if (_Emission > 0.5)
					color += _EmissionTex.Sample(_EmissionTexSampler, input.texCoord0).rgb * _EmissionColor.rgb;

				color = StandardApplyFog(color, input.worldPos);
				return float4(color, 1.0);
			}
		}
	ENDSLANG
}

Pass "ToonShadow"
{
    Tags { "LightMode" = "ShadowCaster" }
    Cull Back

	GLSLPROGRAM

		Vertex
		{
            #include "XEngineCG"
            #include "VertexAttributes"

			out vec2 texCoord0;

			uniform vec2 _Tiling;
			uniform vec2 _Offset;

			void main()
			{
				gl_Position = TransformClip(vertexPosition);
				texCoord0 = vertexTexCoord0 * _Tiling + _Offset;
			}
		}

		Fragment
		{
            #include "XEngineCG"

			in vec2 texCoord0;
			uniform sampler2D _MainTex;
			uniform vec4 _MainColor;
			uniform float _AlphaCutoff;

			void main()
			{
				if (_AlphaCutoff > 0.0)
				{
					float alpha = texture(_MainTex, texCoord0).a * _MainColor.a;
					if (alpha < _AlphaCutoff) discard;
				}
                gl_FragDepth = gl_FragCoord.z;
			}
		}
	ENDGLSL

	SLANGPROGRAM
		Vertex
		{
			#include "XEngineCG"

			cbuffer ShadowMaterial : register(b2)
			{
				float2 _Tiling;
				float2 _Offset;
				float4 _MainColor;
				float _AlphaCutoff;
				float3 _ShadowMaterialPadding;
			};

			struct VSInput
			{
				float3 vertexPosition : POSITION;
				float2 vertexTexCoord0 : TEXCOORD0;
#ifdef HAS_BONEINDICES
				float4 vertexBoneIndices : TEXCOORD4;
				float4 vertexBoneWeights : TEXCOORD5;
#endif
			};

			struct VSOutput
			{
				float4 position : SV_Position;
				float2 texCoord0 : TEXCOORD0;
			};

			VSOutput main(VSInput input)
			{
				VSOutput o;
#if defined(SKINNED) && defined(HAS_BONEINDICES)
				o.position = TransformClipSkinned(input.vertexPosition, input.vertexBoneIndices, input.vertexBoneWeights);
#else
				o.position = TransformClip(input.vertexPosition);
#endif
				o.texCoord0 = input.vertexTexCoord0 * _Tiling + _Offset;
				return o;
			}
		}

		Fragment
		{
			cbuffer ShadowMaterial : register(b2)
			{
				float2 _Tiling;
				float2 _Offset;
				float4 _MainColor;
				float _AlphaCutoff;
				float3 _ShadowMaterialPadding;
			};

			Texture2D _MainTex : register(t0);
			SamplerState _MainTexSampler : register(s0);

			struct PSInput
			{
				float4 position : SV_Position;
				float2 texCoord0 : TEXCOORD0;
			};

			void main(PSInput input)
			{
				if (_AlphaCutoff > 0.0)
				{
					float alpha = _MainTex.Sample(_MainTexSampler, input.texCoord0).a * _MainColor.a;
					if (alpha < _AlphaCutoff) discard;
				}
			}
		}
	ENDSLANG
}
