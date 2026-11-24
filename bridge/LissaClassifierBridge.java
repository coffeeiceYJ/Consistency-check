import java.nio.file.*;
import java.util.*;
import com.fasterxml.jackson.databind.*;
import edu.kit.kastel.mcse.ardoco.llm.cache.CacheManager;
import edu.kit.kastel.mcse.ardoco.llm.util.MapEnvironment;
import edu.kit.kastel.sdq.lissa.ratlr.classifier.SimpleClassifier;
import edu.kit.kastel.sdq.lissa.ratlr.classifier.ClassificationTask;
import edu.kit.kastel.sdq.lissa.ratlr.configuration.ModuleConfiguration;
import edu.kit.kastel.sdq.lissa.ratlr.context.ContextStore;
import edu.kit.kastel.sdq.lissa.ratlr.knowledge.Element;

/** Original LiSSA SimpleClassifier, using a local metered OpenAI-compatible transport. */
public class LissaClassifierBridge {
    static Element element(JsonNode n, String type, ObjectMapper mapper) throws Exception {
        return new Element(n.get("id").asText(),type,mapper.writeValueAsString(n),0,(Element)null,true);
    }
    public static void main(String[] args) throws Exception {
        var mapper=new ObjectMapper();
        var data=mapper.readTree(Path.of(args[0]).toFile());
        var env=new MapEnvironment(Map.of("BIFROST_URL",data.get("gateway").asText(),
                "BIFROST_API_KEY",data.get("local_token").asText(),"CACHE_HIERARCHY","LOCAL"));
        CacheManager.setCacheDir(args[2],env);
        var config=new ModuleConfiguration("simple_bifrost",Map.of("model",data.get("model").asText(),
                "temperature","0","template","SOURCE_JSON\n{source_content}\nTARGET_JSON\n{target_content}"));
        var classifier=new SimpleClassifier(config,new ContextStore(),env);
        var results=new ArrayList<Map<String,Object>>();
        for(var pair:data.get("pairs")) {
            var source=element(pair.get("source"),"requirement",mapper);
            var target=element(pair.get("target"),"model",mapper);
            var result=classifier.classify(new ClassificationTask(source,target,false));
            results.add(Map.of("requirement_id",source.getIdentifier(),"model_id",target.getIdentifier(),"linked",result.isPresent()));
        }
        mapper.writeValue(Path.of(args[1]).toFile(),results);
    }
}
