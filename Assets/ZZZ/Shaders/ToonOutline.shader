Shader "Zonezero/ToonOutline"

Variants
{
}

// Inverted-hull outline ported from the ZZZ reference project's YSA "Perfect Outline"
// (zonezero M3). A back-face hull grows outward along the vertex normal; width uses the YSA
// CameraFixMultiplier so it stays constant in screen space across depth (perspective scales
// with |viewZ| * fovY * 1e-4; orthographic with orthoHeight * 125 * 1e-4). When the mesh
// carries NormalsFix-baked smoothed normals in its vertex colors (RGB), those are used
// instead of the interpolated vertex normal — hard-edged meshes stop showing outline seams.
//
// Draw as an additional material on the SAME mesh (Cull Front = render back faces only),
// while the lit Default/Toon pass shrinks inward via _ShrinkSize so the hull shows cleanly.

Properties
{
    _OutlineColor ("Outline Color", Color) = (0.0, 0.0, 0.0, 1.0)
    _OutlineWidth ("Outline Width", Float) = 0.1
    _VertexColorNormals ("Use Vertex-Color Smoothed Normals", Float) = 0.0
}

Pass "Outline"
{
    Tags { "RenderOrder" = "Opaque" }
    Cull Front

	GLSLPROGRAM

		Vertex
		{
            #include "XEngineCG"
            #include "VertexAttributes"

			uniform float _OutlineWidth;
			uniform float _VertexColorNormals;

			void main()
			{
				vec3 worldPos = TransformPosition(vertexPosition);
				vec3 worldNormal;
				if (_VertexColorNormals > 0.5)
					worldNormal = TransformDirection(normalize(vertexColor.rgb));
				else
					worldNormal = TransformDirection(GetMorphedNormal(vertexNormal));

				// YSA CameraFixMultiplier (see Default/Toon's shrink twin for commentary).
				vec4 clip = xengine_MatVP * vec4(worldPos, 1.0);
				float cameraFix;
				if (xengine_MatP[3][3] < 0.5) {
					float fovY = 2.0 * atan(1.0 / xengine_MatP[1][1]) * 57.2957795;
					cameraFix = clamp(abs(clip.w), 0.0, 4.0) * fovY * 0.0001;
				} else {
					float orthoHeight = 1.0 / xengine_MatP[1][1];
					cameraFix = orthoHeight * 125.0 * 0.0001;
				}

				vec3 expanded = worldPos + normalize(worldNormal) * (_OutlineWidth * cameraFix);
				vec3 objectPos = (xengine_WorldToObject * vec4(expanded, 1.0)).xyz;
				gl_Position = TransformClip(objectPos);
			}
		}

		Fragment
		{
            #include "XEngineCG"

			layout (location = 0) out vec4 fragColor;

			uniform vec4 _OutlineColor;

			void main()
			{
				fragColor = _OutlineColor;
			}
		}
	ENDGLSL

	SLANGPROGRAM
		Vertex
		{
			#include "XEngineCG"

			cbuffer ToonOutlineMaterial : register(b2)
			{
				float4 _OutlineColor;
				float _OutlineWidth;
				float _VertexColorNormals;
				float2 _ToonOutlinePadding;
			};

			struct VSInput
			{
				float3 vertexPosition : POSITION;
				float3 vertexNormal : NORMAL;
				float4 vertexColor : COLOR0;
#ifdef HAS_BONEINDICES
				float4 vertexBoneIndices : TEXCOORD4;
				float4 vertexBoneWeights : TEXCOORD5;
#endif
			};

			struct VSOutput
			{
				float4 position : SV_Position;
			};

			VSOutput main(VSInput input)
			{
				VSOutput o;
#if defined(SKINNED) && defined(HAS_BONEINDICES)
				float3 worldPos = TransformPositionSkinned(input.vertexPosition, input.vertexBoneIndices, input.vertexBoneWeights);
#else
				float3 worldPos = TransformPosition(input.vertexPosition);
#endif
				float3 worldNormal;
				if (_VertexColorNormals > 0.5)
					worldNormal = TransformDirection(normalize(input.vertexColor.rgb));
				else
					worldNormal = TransformDirection(GetMorphedNormal(input.vertexNormal));

				// YSA CameraFixMultiplier (see Default/Toon's shrink twin for commentary).
				float4 clip = mul(XENGINE_MATRIX_VP, float4(worldPos, 1.0));
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

				float3 expanded = worldPos + normalize(worldNormal) * (_OutlineWidth * cameraFix);
				float3 objectPos = mul(xengine_WorldToObject, float4(expanded, 1.0)).xyz;
				o.position = TransformClip(objectPos);
				return o;
			}
		}

		Fragment
		{
			#include "XEngineCG"

			cbuffer ToonOutlineMaterial : register(b2)
			{
				float4 _OutlineColor;
				float _OutlineWidth;
				float _VertexColorNormals;
				float2 _ToonOutlinePadding;
			};

			float4 main() : SV_Target
			{
				return _OutlineColor;
			}
		}
	ENDSLANG
}
